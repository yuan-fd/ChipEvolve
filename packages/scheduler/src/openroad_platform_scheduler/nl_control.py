"""Deterministic NL task compiler and evidence-bounded repair policy."""

from __future__ import annotations

import dataclasses
import re
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from openroad_platform_contracts import RepairAction, TaskSpec
from openroad_platform_execution import build_orfs_task, build_rtlscout_task


UNSAFE_TEXT = re.compile(r"[;&|`$<>]|\b(?:rm|curl|wget|bash|sh|sudo)\b", re.I)
NUMBER = r"(\d+(?:\.\d+)?)"


class NaturalLanguageTaskCompiler:
    """Compile a deliberately small language into validated platform tasks."""

    def compile(
        self, text: str, *, project_id: str, design_id: str,
        rtl_path: str | Path | None = None, top: str | None = None,
    ) -> TaskSpec:
        intent = " ".join(str(text).split())
        if not intent or len(intent) > 2000:
            raise ValueError("Natural-language intent is empty or too long")
        if UNSAFE_TEXT.search(intent):
            raise ValueError("Intent contains forbidden command syntax")
        lowered = intent.lower()
        if "asap" in lowered or "sky130" in lowered:
            raise ValueError("Only the allowlisted nangate45 platform is available")
        if "rtlscout" in lowered:
            benchmark = "simple_adder" if "simple_adder" in lowered else None
            if benchmark is None:
                raise ValueError("RTLScout benchmark is not allowlisted")
            model_match = re.search(
                r"\b(fake|anthropic|deepinfra|openrouter):([A-Za-z0-9_.-]+)", intent,
                re.I,
            )
            if model_match:
                model = f"{model_match.group(1).lower()}:{model_match.group(2)}"
            elif "fake" in lowered or "offline" in lowered or "离线" in intent:
                model = "fake:simple_adder_pass"
            else:
                raise ValueError("RTLScout intent requires an explicit allowlisted model")
            steps = self._integer(intent, r"(?:steps?|步)", default=20, minimum=1, maximum=100)
            return build_rtlscout_task(
                project_id=project_id, design_id=design_id, benchmark=benchmark,
                model=model, max_steps=steps,
            )
        if not any(token in lowered for token in ("orfs", "gds", "openroad")) and not any(
            token in intent for token in ("芯片", "布线", "综合")
        ):
            raise ValueError("Intent does not select an allowlisted workflow")
        if rtl_path is None:
            raise ValueError("ORFS intent requires a registered RTL artifact context")
        stage = "finish"
        if "综合" in intent or "synthesis" in lowered or " synth" in f" {lowered}":
            stage = "synth"
        elif "floorplan" in lowered or "布局规划" in intent:
            stage = "floorplan"
        elif "place" in lowered or "放置" in intent:
            stage = "place"
        elif ("route" in lowered or "布线" in intent) and "gds" not in lowered:
            stage = "route"
        period = self._float(intent, rf"{NUMBER}\s*ns", default=10.0,
                             minimum=0.01, maximum=1000.0)
        utilization = self._float(intent, rf"{NUMBER}\s*%", default=10.0,
                                  minimum=1.0, maximum=99.0)
        task = build_orfs_task(
            rtl_path, project_id=project_id, design_id=design_id, top=top,
            platform_name="nangate45", target_stage=stage,
            clock_period_ns=period, core_utilization_pct=utilization,
        )
        task.validate()
        return task

    @staticmethod
    def _float(text: str, pattern: str, *, default: float,
               minimum: float, maximum: float) -> float:
        match = re.search(pattern, text, re.I)
        value = float(match.group(1)) if match else default
        if not minimum <= value <= maximum:
            raise ValueError(f"Numeric intent value {value} is outside policy")
        return value

    @staticmethod
    def _integer(text: str, suffix: str, *, default: int,
                 minimum: int, maximum: int) -> int:
        match = re.search(rf"(\d+)\s*{suffix}", text, re.I)
        value = int(match.group(1)) if match else default
        if not minimum <= value <= maximum:
            raise ValueError(f"Integer intent value {value} is outside policy")
        return value


class LimitedReActController:
    """Map structured failures to data-only repairs with hard stop conditions."""

    def __init__(self, *, max_repairs: int = 2, max_same_failure: int = 2):
        if not 0 <= max_repairs <= 10 or not 1 <= max_same_failure <= 3:
            raise ValueError("Invalid repair budget")
        self.max_repairs = max_repairs
        self.max_same_failure = max_same_failure

    def decide(
        self, task: TaskSpec, failure: Mapping[str, object],
        history: Sequence[RepairAction] = (),
    ) -> RepairAction:
        task.validate()
        category = str(failure.get("category") or "unknown")
        refs = tuple(str(item) for item in failure.get("evidence_refs", ()) if str(item))
        if not refs:
            raise ValueError("Repair decisions require evidence_refs")
        used = sum(action.action_type != "stop" for action in history)
        same = sum(action.reason_code == category for action in history)
        if used >= self.max_repairs or same >= self.max_same_failure:
            return self._action("stop", "repair_budget_exhausted",
                                {"terminal_reason": "repair_budget_exhausted"}, refs)
        if category in {"worker_lost", "transient_io"}:
            return self._action("retry", category, {}, refs)
        if category == "timeout":
            target = min(86400, task.timeout_seconds * 2)
            if target == task.timeout_seconds:
                return self._action("stop", category,
                                    {"terminal_reason": "timeout_limit_reached"}, refs)
            return self._action("increase_timeout", category,
                                {"timeout_seconds": target}, refs)
        if category in {"congestion", "placement_failed"} and task.plugin_id == "orfs":
            current = float(task.parameters.get("core_utilization_pct", 10.0))
            target = max(1.0, current - 5.0)
            if target == current:
                return self._action("stop", category,
                                    {"terminal_reason": "utilization_limit_reached"}, refs)
            return self._action("lower_core_utilization", category,
                                {"core_utilization_pct": target}, refs)
        return self._action("stop", category,
                            {"terminal_reason": "failure_not_repairable"}, refs)

    def apply(self, task: TaskSpec, action: RepairAction) -> TaskSpec:
        task.validate()
        action.validate()
        if action.action_type == "stop":
            raise ValueError("A stop action cannot create another TaskSpec")
        changes: dict[str, object] = {
            "task_id": f"repair-{uuid.uuid4().hex}",
            "labels": {**task.labels, "repair_action_id": action.action_id,
                       "repair_reason": action.reason_code},
        }
        if action.action_type == "retry":
            changes["max_attempts"] = min(3, task.max_attempts + 1)
        elif action.action_type == "increase_timeout":
            changes["timeout_seconds"] = action.parameters["timeout_seconds"]
        elif action.action_type == "lower_core_utilization":
            changes["parameters"] = {
                **task.parameters,
                "core_utilization_pct": action.parameters["core_utilization_pct"],
            }
        result = dataclasses.replace(task, **changes)
        result.validate()
        return result

    @staticmethod
    def _action(action_type: str, reason: str, parameters: dict,
                refs: tuple[str, ...]) -> RepairAction:
        action = RepairAction(action_id=f"repair-{uuid.uuid4().hex}",
                              action_type=action_type, reason_code=reason,
                              parameters=parameters, evidence_refs=refs)
        action.validate()
        return action
