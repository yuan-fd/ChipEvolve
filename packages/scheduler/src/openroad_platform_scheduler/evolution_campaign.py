"""Runtime-driven parameter evolution campaign with bounded automatic turns.

The controller never invokes a shell or edits code.  It submits only copies of
the declared baseline TaskSpec with one allowlisted parameter adjusted, and
records every terminal run (including failures) before proposing the next turn.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_contracts import TaskSpec

from .runtime import WorkflowRuntime


TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


@dataclass(frozen=True)
class EvolutionCampaign:
    campaign_id: str
    baseline_task: TaskSpec
    parameter: str
    values: tuple[float, ...]
    repetitions: int
    max_rounds: int
    stall_window: int
    objective_metric: str
    redirect_parameter: str | None = None
    redirect_values: tuple[float, ...] = ()

    def validate(self) -> None:
        self.baseline_task.validate()
        if self.parameter not in self.baseline_task.parameters:
            raise ValueError("Evolution parameter must be declared by baseline TaskSpec")
        if not self.values or len(self.values) > 64 or not all(isinstance(x, (int, float)) for x in self.values):
            raise ValueError("Evolution values must be a bounded numeric list")
        if not 1 <= self.repetitions <= 10 or not 1 <= self.max_rounds <= 64 or not 1 <= self.stall_window <= 10:
            raise ValueError("Invalid evolution budget")
        if not self.objective_metric or len(self.objective_metric) > 256:
            raise ValueError("objective_metric is required")
        if self.redirect_parameter is None:
            if self.redirect_values:
                raise ValueError("redirect_values require a redirect_parameter")
        else:
            if self.redirect_parameter == self.parameter:
                raise ValueError("redirect_parameter must differ from primary parameter")
            if self.redirect_parameter not in self.baseline_task.parameters:
                raise ValueError("Redirect parameter must be declared by baseline TaskSpec")
            if (not self.redirect_values or len(self.redirect_values) > 64
                    or not all(isinstance(x, (int, float)) for x in self.redirect_values)):
                raise ValueError("Redirect values must be a bounded numeric list")


class EvolutionCampaignStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve(); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS evolution_campaigns_v1 (
                campaign_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, state_json TEXT NOT NULL)""")

    def create(self, campaign: EvolutionCampaign) -> None:
        campaign.validate()
        state = {"status": "baseline_pending", "baseline_run_ids": [], "round": 0,
                 "active_run_ids": [], "history": [], "stalled_rounds": 0,
                 "redirect": None, "redirect_used": False, "best_value": None}
        with self._connect() as c:
            c.execute("INSERT INTO evolution_campaigns_v1 VALUES (?, ?, ?)", (
                campaign.campaign_id, json.dumps(_config(campaign), sort_keys=True), json.dumps(state, sort_keys=True)))

    def get(self, campaign_id: str) -> tuple[EvolutionCampaign, dict[str, Any]]:
        with self._connect() as c:
            row = c.execute("SELECT config_json,state_json FROM evolution_campaigns_v1 WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row is None: raise KeyError(campaign_id)
        value = json.loads(row[0]); task = TaskSpec.from_dict(value.pop("baseline_task"))
        return EvolutionCampaign(baseline_task=task, **value), json.loads(row[1])

    def save(self, campaign_id: str, state: dict[str, Any]) -> None:
        with self._connect() as c:
            if c.execute("UPDATE evolution_campaigns_v1 SET state_json=? WHERE campaign_id=?", (json.dumps(state, sort_keys=True), campaign_id)).rowcount != 1:
                raise KeyError(campaign_id)

    def _connect(self): return sqlite3.connect(self.path)


class EvolutionCampaignController:
    def __init__(self, store: EvolutionCampaignStore, runtime: WorkflowRuntime): self.store,self.runtime=store,runtime

    def start(self, campaign: EvolutionCampaign) -> dict[str, Any]:
        self.store.create(campaign); _, state = self.store.get(campaign.campaign_id)
        state["baseline_run_ids"] = [self.runtime.submit(_replica(campaign.baseline_task, campaign.campaign_id, "baseline", i)).run_id for i in range(campaign.repetitions)]
        state["status"] = "baseline_running"; self.store.save(campaign.campaign_id, state)
        return self.describe(campaign.campaign_id)

    def advance(self, campaign_id: str, *, execute: bool = True) -> dict[str, Any]:
        campaign,state=self.store.get(campaign_id)
        run_ids=list(state["baseline_run_ids"])+list(state["active_run_ids"])
        if execute:
            for run_id in run_ids:
                run=self.runtime.store.get_run(run_id)
                if run.status.value not in TERMINAL: self.runtime.execute_once(run_id)
        if state["status"] == "baseline_running":
            if not _all_terminal(self.runtime, state["baseline_run_ids"]): return self.describe(campaign_id)
            baseline = _summary(self.runtime, state["baseline_run_ids"], "baseline", campaign.objective_metric)
            state["history"].append(baseline)
            if isinstance(baseline.get("median"), (int, float)):
                state["best_value"] = baseline["median"]
            state["status"]="ready"; self.store.save(campaign_id,state)
        if state["status"] == "round_running":
            if not _all_terminal(self.runtime, state["active_run_ids"]): return self.describe(campaign_id)
            item=_summary(self.runtime,state["active_run_ids"],f"round-{state['round']}",campaign.objective_metric)
            state["history"].append(item); state["active_run_ids"]=[]
            value=item.get("median")
            improved=isinstance(value,(int,float)) and (state["best_value"] is None or value < state["best_value"])
            if improved: state["best_value"],state["stalled_rounds"]=value,0
            else: state["stalled_rounds"]+=1
            state["status"]="ready"; self.store.save(campaign_id,state)
        if state["status"] == "redirect_running":
            if not _all_terminal(self.runtime, state["active_run_ids"]): return self.describe(campaign_id)
            item=_summary(self.runtime,state["active_run_ids"],f"redirect-{state['round']}",campaign.objective_metric)
            state["history"].append(item); state["active_run_ids"]=[]
            value=item.get("median")
            improved=isinstance(value,(int,float)) and (state["best_value"] is None or value < state["best_value"])
            if improved: state["best_value"]=value
            state["status"]="completed"; self.store.save(campaign_id,state)
        if state["status"] == "ready":
            if state["stalled_rounds"] >= campaign.stall_window:
                if campaign.redirect_parameter is not None and not state["redirect_used"]:
                    state["redirect_used"] = True
                    state["round"] += 1
                    phase = f"redirect-{state['round']}"
                    state["redirect"] = {
                        "reason": "stall_window_reached", "next": "declared_parameter_redirect",
                        "parameter": campaign.redirect_parameter,
                        "values": list(campaign.redirect_values), "automatic_execution": True,
                        "scope": "predeclared_parameter_only",
                    }
                    state["active_run_ids"] = [self.runtime.submit(_replica(
                        TaskSpec.from_dict({**campaign.baseline_task.to_dict(),
                            "task_id": f"evolution-{campaign_id}-{phase}",
                            "parameters": {**campaign.baseline_task.parameters,
                                           campaign.redirect_parameter: value}}),
                        campaign_id, phase, replica * len(campaign.redirect_values) + value_index
                    )).run_id for value_index, value in enumerate(campaign.redirect_values)
                        for replica in range(campaign.repetitions)]
                    state["status"] = "redirect_running"; self.store.save(campaign_id,state)
                    return self.describe(campaign_id)
                state["status"]="diagnosis_required"; state["redirect"]={"reason":"stall_window_reached","next":"diagnosis_then_reviewed_repair","automatic_execution":False}; self.store.save(campaign_id,state); return self.describe(campaign_id)
            if state["round"] >= campaign.max_rounds or state["round"] >= len(campaign.values):
                state["status"]="completed"; self.store.save(campaign_id,state); return self.describe(campaign_id)
            value=campaign.values[state["round"]]; state["round"]+=1
            task=TaskSpec.from_dict({**campaign.baseline_task.to_dict(),"task_id":f"evolution-{campaign_id}-{state['round']}","parameters":{**campaign.baseline_task.parameters,campaign.parameter:value}})
            state["active_run_ids"]=[self.runtime.submit(_replica(task,campaign_id,f"round-{state['round']}",i)).run_id for i in range(campaign.repetitions)]
            state["status"]="round_running"; self.store.save(campaign_id,state)
        return self.describe(campaign_id)

    def describe(self,campaign_id:str)->dict[str,Any]:
        campaign,state=self.store.get(campaign_id); return {"campaign":_config(campaign),"state":state,"runtime_authority":True,"automatic_scope":"declared parameter only; diagnosis redirect requires review"}


def _config(c: EvolutionCampaign)->dict[str,Any]: return {"campaign_id":c.campaign_id,"baseline_task":c.baseline_task.to_dict(),"parameter":c.parameter,"values":list(c.values),"repetitions":c.repetitions,"max_rounds":c.max_rounds,"stall_window":c.stall_window,"objective_metric":c.objective_metric,"redirect_parameter":c.redirect_parameter,"redirect_values":list(c.redirect_values)}
def _replica(t:TaskSpec,cid:str,phase:str,index:int)->TaskSpec:return TaskSpec.from_dict({**t.to_dict(),"task_id":f"{t.task_id}-rep-{index}-{uuid.uuid4().hex[:8]}","labels":{**t.labels,"evolution_campaign_id":cid,"evolution_phase":phase,"replica_index":str(index)}})
def _all_terminal(runtime:WorkflowRuntime,ids:list[str])->bool:return all(runtime.store.get_run(x).status.value in TERMINAL for x in ids)
def _summary(runtime:WorkflowRuntime,ids:list[str],phase:str,metric:str)->dict[str,Any]:
    values=[]; statuses=[]
    for run_id in ids:
        view=runtime.describe(run_id); statuses.append(view["run"]["status"])
        for stage in view["stages"]:
            for attempt in stage["attempts"]:
                for item in attempt["metrics"]:
                    if item["name"]==metric and isinstance(item["value"],(int,float)): values.append(float(item["value"]))
    values.sort(); median=values[len(values)//2] if values else None
    return {"phase":phase,"run_ids":ids,"statuses":statuses,"values":values,"median":median,"failure_rate":round(sum(x!="succeeded" for x in statuses)/len(statuses),6)}
