"""Durable, bounded multi-turn specification proposals for Spec-to-GDS.

Providers may propose structured data, but only the deterministic compiler and
Workflow Runtime are allowed to create executable work.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from openroad_platform_contracts import PortSpec, TaskSpec


ALLOWED_MODELS = {"gpt-5.6-terra"}
ALLOWED_STAGES = {"synth", "floorplan", "place", "cts", "route", "finish"}
# These are the normal 2D ORFS platforms available to v2.  TaiWei 3D remains
# a separately pinned, explicit flow and is intentionally not guessed from
# natural language here.
ALLOWED_PLATFORMS = {"nangate45", "sky130hd", "sky130hs", "asap7", "gf180"}
SPEC_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SpecProposal:
    objective: str
    functionality: str
    top: str | None
    clock: str | None
    reset: str | None
    target_platform: str
    target_stage: str
    clock_period_ns: float
    core_utilization_pct: float
    place_density: float
    ports: tuple[PortSpec, ...]
    missing_fields: tuple[str, ...]
    assumptions: tuple[str, ...]
    clarification_questions: tuple[str, ...]
    ready_for_execution: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SpecProposal":
        if "rtl_source" in value:
            raise ValueError("Spec proposals cannot include RTL source; use RTLScout-v2")
        platform = str(value.get("target_platform") or "nangate45").lower()
        stage = str(value.get("target_stage") or "finish").lower()
        if platform not in ALLOWED_PLATFORMS:
            raise ValueError(f"Unsupported target platform: {platform}")
        if stage not in ALLOWED_STAGES:
            raise ValueError(f"Unsupported target stage: {stage}")
        period = _bounded_float(value.get("clock_period_ns", 10.0), 0.01, 1000.0,
                                "clock_period_ns")
        utilization = _bounded_float(value.get("core_utilization_pct", 10.0), 1.0, 99.0,
                                     "core_utilization_pct")
        density = _bounded_float(value.get("place_density", 0.45), 0.01, 1.0,
                                 "place_density")
        ports = _ports(value.get("ports"))
        missing = _strings(value.get("missing_fields"), maximum=20)
        questions = _strings(value.get("clarification_questions"), maximum=10)
        ready = bool(value.get("ready_for_execution")) and not missing and not questions
        result = cls(
            objective=_text(value.get("objective"), maximum=2000),
            functionality=_text(value.get("functionality"), maximum=8000),
            top=_identifier(value.get("top")),
            clock=_identifier(value.get("clock")),
            reset=_identifier(value.get("reset")),
            target_platform=platform,
            target_stage=stage,
            clock_period_ns=period,
            core_utilization_pct=utilization,
            place_density=density,
            ports=ports,
            missing_fields=missing,
            assumptions=_strings(value.get("assumptions"), maximum=20),
            clarification_questions=questions,
            ready_for_execution=ready,
        )
        if result.ready_for_execution and (not result.top or not result.ports):
            raise ValueError("A ready SpecIR proposal requires top and declared ports")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective, "functionality": self.functionality,
            "top": self.top, "clock": self.clock, "reset": self.reset,
            "target_platform": self.target_platform, "target_stage": self.target_stage,
            "clock_period_ns": self.clock_period_ns,
            "core_utilization_pct": self.core_utilization_pct,
            "place_density": self.place_density,
            "ports": [item.to_dict() for item in self.ports],
            "missing_fields": list(self.missing_fields),
            "assumptions": list(self.assumptions),
            "clarification_questions": list(self.clarification_questions),
            "ready_for_execution": self.ready_for_execution,
        }


class SpecProvider(Protocol):
    provider_name: str
    model: str

    def propose(self, messages: Sequence[Mapping[str, str]], current: Mapping[str, Any],
                *, design_context: Mapping[str, Any] | None = None) -> SpecProposal: ...


class RuleBasedSpecProvider:
    """Offline baseline used for tests and when no model is configured."""

    provider_name = "deterministic"
    model = "rules-v1"

    def propose(self, messages: Sequence[Mapping[str, str]], current: Mapping[str, Any],
                *, design_context: Mapping[str, Any] | None = None) -> SpecProposal:
        text = " ".join(item["content"] for item in messages if item.get("role") == "user")
        lowered = text.lower()
        state = dict(current)
        state["objective"] = _text(state.get("objective") or text, maximum=2000)
        state["functionality"] = _text(state.get("functionality") or text, maximum=8000)
        if design_context:
            state["top"] = design_context.get("module") or state.get("top")
            analysis = design_context.get("analysis") or {}
            if not state.get("ports"):
                state["ports"] = (
                    [{"name": name, "direction": "input"}
                     for name in analysis.get("inputs", ())]
                    + [{"name": name, "direction": "output"}
                       for name in analysis.get("outputs", ())]
                )
        top_match = re.search(r"(?:top|顶层(?:模块)?)\s*(?:是|为|=|:)?\s*([A-Za-z_]\w*)", text, re.I)
        if top_match:
            state["top"] = top_match.group(1)
        clock_match = re.search(r"(?:clock|时钟)\s*(?:是|为|=|:)?\s*([A-Za-z_]\w*)", text, re.I)
        if clock_match:
            state["clock"] = clock_match.group(1)
        period_match = re.search(r"(\d+(?:\.\d+)?)\s*ns", text, re.I)
        util_match = re.search(r"(?:利用率|utili[sz]ation)\D{0,12}(\d+(?:\.\d+)?)\s*%", text, re.I)
        density_match = re.search(r"(?:密度|density)\D{0,12}(0(?:\.\d+)?|1(?:\.0+)?)", text, re.I)
        state["clock_period_ns"] = float(period_match.group(1)) if period_match else state.get("clock_period_ns", 10.0)
        state["core_utilization_pct"] = float(util_match.group(1)) if util_match else state.get("core_utilization_pct", 10.0)
        state["place_density"] = float(density_match.group(1)) if density_match else state.get("place_density", 0.45)
        platform_aliases = (("sky130hs", "sky130hs"), ("sky130hd", "sky130hd"),
                            ("sky130", "sky130hd"), ("asap7", "asap7"),
                            ("gf180", "gf180"), ("nangate45", "nangate45"))
        state["target_platform"] = next((platform for token, platform in platform_aliases
                                         if token in lowered),
                                        state.get("target_platform", "nangate45"))
        stage = next((item for item in ("synth", "floorplan", "place", "cts", "route")
                      if item in lowered), "finish")
        if "gds" in lowered:
            stage = "finish"
        state["target_stage"] = stage
        missing = []
        if not state.get("top"):
            missing.append("top")
        if not state.get("ports"):
            missing.append("ports")
        state["missing_fields"] = missing
        state["assumptions"] = [f"使用已准入的 {state['target_platform']} ORFS 工具链"]
        state["clarification_questions"] = [
            "请提供模块端口、方向和位宽；RTL 将由 RTLScout-v2 在验证包约束下生成。"
        ] if "ports" in missing else (["请确认顶层模块名。"] if "top" in missing else [])
        state["ready_for_execution"] = not missing
        return SpecProposal.from_mapping(state)


class CodexCliSpecProvider:
    """Local, ephemeral structured-output provider backed by the Codex CLI."""

    provider_name = "codex-cli"

    def __init__(self, *, model: str = "gpt-5.6-terra", timeout_seconds: int = 180,
                 executable: str | Path | None = None):
        if model not in ALLOWED_MODELS:
            raise ValueError(f"Model is not allowlisted: {model}")
        if not 1 <= timeout_seconds <= 600:
            raise ValueError("Codex timeout must be between 1 and 600 seconds")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.executable = str(executable or shutil.which("codex") or "")
        if not self.executable:
            raise FileNotFoundError("codex CLI is unavailable")

    def propose(self, messages: Sequence[Mapping[str, str]], current: Mapping[str, Any],
                *, design_context: Mapping[str, Any] | None = None) -> SpecProposal:
        prompt = (
            "You are a constrained ASIC specification compiler. Return only the JSON object "
            "required by the supplied schema. Merge the conversation into a conservative draft. "
            f"Only target one of {sorted(ALLOWED_PLATFORMS)} and one of synth/floorplan/place/cts/route/finish. "
            "Never generate RTL source: your output is SpecIR only. Extract every module port with direction "
            "and width when stated; otherwise ask a clarification question. Never invoke tools. "
            "Ask concise clarification questions for ambiguous functional behavior. Defaults may be "
            "10ns, 10% core utilization, 0.45 placement density and must be listed as assumptions.\n\n"
            f"CURRENT={json.dumps(dict(current), ensure_ascii=False)}\n"
            f"DESIGN_CONTEXT={json.dumps(dict(design_context or {}), ensure_ascii=False)}\n"
            f"MESSAGES={json.dumps(list(messages), ensure_ascii=False)}"
        )
        with tempfile.TemporaryDirectory(prefix="openroad-spec-provider-") as raw:
            root = Path(raw)
            schema_path = root / "schema.json"
            output_path = root / "proposal.json"
            schema_path.write_text(json.dumps(_proposal_schema()), encoding="utf-8")
            env = {key: os.environ[key] for key in (
                "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TZ", "CODEX_HOME"
            ) if key in os.environ}
            result = subprocess.run(
                [self.executable, "exec", "--ephemeral", "--ignore-rules",
                 "--skip-git-repo-check", "--sandbox", "read-only", "--model", self.model,
                 "--output-schema", str(schema_path), "--output-last-message", str(output_path),
                 "--color", "never", "-"],
                input=prompt, cwd=root, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout_seconds, check=False,
            )
            if result.returncode != 0 or not output_path.is_file():
                detail = "\n".join((result.stderr or result.stdout).splitlines()[-10:])
                raise RuntimeError(detail or "Codex provider returned no structured proposal")
            try:
                value = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("Codex provider returned invalid JSON") from exc
        return SpecProposal.from_mapping(value)


class SpecConversationStore:
    """SQLite session heads with append-only user/provider turns."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS spec_sessions (
                    session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    design_id TEXT, provider TEXT NOT NULL, model TEXT NOT NULL,
                    status TEXT NOT NULL, state_json TEXT NOT NULL,
                    max_turns INTEGER NOT NULL, max_llm_calls INTEGER NOT NULL,
                    max_eda_runs INTEGER NOT NULL, max_repairs INTEGER NOT NULL,
                    wall_clock_seconds INTEGER NOT NULL, llm_calls INTEGER NOT NULL DEFAULT 0,
                    eda_runs INTEGER NOT NULL DEFAULT 0, repairs INTEGER NOT NULL DEFAULT 0,
                    run_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spec_turns (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_id TEXT NOT NULL UNIQUE, session_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, proposal_json TEXT,
                    provider TEXT, model TEXT, created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES spec_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_spec_turn_session
                    ON spec_turns(session_id, sequence);
            """)

    def create(self, *, project_id: str, design_id: str | None, provider: str, model: str,
               budgets: Mapping[str, Any] | None = None) -> str:
        limits = {"max_turns": 8, "max_llm_calls": 8, "max_eda_runs": 3,
                  "max_repairs": 2, "wall_clock_seconds": 86_400}
        limits.update(dict(budgets or {}))
        for key, ceiling in (("max_turns", 20), ("max_llm_calls", 20),
                             ("max_eda_runs", 10), ("max_repairs", 5),
                             ("wall_clock_seconds", 604_800)):
            value = limits[key]
            if not isinstance(value, int) or not 1 <= value <= ceiling:
                raise ValueError(f"Invalid {key} budget")
        now = _now()
        session_id = f"spec-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO spec_sessions
                   (session_id, project_id, design_id, provider, model, status, state_json,
                    max_turns, max_llm_calls, max_eda_runs, max_repairs, wall_clock_seconds,
                    created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'draft', '{}', ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, project_id, design_id, provider, model, limits["max_turns"],
                 limits["max_llm_calls"], limits["max_eda_runs"], limits["max_repairs"],
                 limits["wall_clock_seconds"], now, now),
            )
        return session_id

    def get(self, session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM spec_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown spec session: {session_id}")
            turns = connection.execute(
                "SELECT * FROM spec_turns WHERE session_id = ? ORDER BY sequence", (session_id,)
            ).fetchall()
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        result["turns"] = [
            {"sequence": item["sequence"], "turn_id": item["turn_id"],
             "role": item["role"], "content": item["content"],
             "proposal": json.loads(item["proposal_json"]) if item["proposal_json"] else None,
             "provider": item["provider"], "model": item["model"],
             "created_at": item["created_at"]} for item in turns
        ]
        return result

    def append_exchange(self, session_id: str, user_text: str, proposal: SpecProposal,
                        *, provider: str, model: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM spec_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown spec session: {session_id}")
            self._check_budget(row, llm=True)
            turn_count = connection.execute(
                "SELECT COUNT(*) FROM spec_turns WHERE session_id = ? AND role = 'user'",
                (session_id,),
            ).fetchone()[0]
            if turn_count >= row["max_turns"]:
                raise ValueError("Spec turn budget exhausted")
            for role, content, encoded in (
                ("user", user_text, None),
                ("assistant", json.dumps(proposal.to_dict(), ensure_ascii=False),
                 json.dumps(proposal.to_dict(), ensure_ascii=False)),
            ):
                connection.execute(
                    """INSERT INTO spec_turns
                       (turn_id, session_id, role, content, proposal_json, provider, model, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"turn-{uuid.uuid4().hex}", session_id, role, content, encoded,
                     provider if role == "assistant" else None,
                     model if role == "assistant" else None, now),
                )
            status = "ready" if proposal.ready_for_execution else "clarification_required"
            connection.execute(
                """UPDATE spec_sessions SET state_json = ?, status = ?, llm_calls = llm_calls + 1,
                   updated_at = ? WHERE session_id = ?""",
                (json.dumps(proposal.to_dict(), ensure_ascii=False), status, now, session_id),
            )

    def bind_run(self, session_id: str, run_id: str, *, design_id: str | None = None) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM spec_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown spec session: {session_id}")
            if row["run_id"] == run_id:
                return
            self._check_budget(row, eda=True)
            if row["run_id"] and row["run_id"] != run_id:
                raise ValueError("Spec session is already bound to another Runtime run")
            connection.execute(
                """UPDATE spec_sessions SET run_id = ?, design_id = COALESCE(?, design_id),
                   status = 'executing', eda_runs = eda_runs + 1, updated_at = ?
                   WHERE session_id = ?""",
                (run_id, design_id, now, session_id),
            )

    def bind_design(self, session_id: str, design_id: str) -> None:
        """Register reviewed generated RTL without starting physical implementation."""
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT design_id FROM spec_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown spec session: {session_id}")
            if row["design_id"] and row["design_id"] != design_id:
                raise ValueError("Spec session is already bound to another design")
            connection.execute(
                """UPDATE spec_sessions SET design_id = ?, status = 'design_registered',
                   updated_at = ? WHERE session_id = ?""",
                (design_id, now, session_id),
            )

    @staticmethod
    def _check_budget(row: sqlite3.Row, *, llm: bool = False, eda: bool = False) -> None:
        age = time.time() - datetime.fromisoformat(row["created_at"]).timestamp()
        if age > row["wall_clock_seconds"]:
            raise ValueError("Spec session wall-clock budget exhausted")
        if llm and row["llm_calls"] >= row["max_llm_calls"]:
            raise ValueError("Spec LLM-call budget exhausted")
        if eda and row["eda_runs"] >= row["max_eda_runs"]:
            raise ValueError("Spec EDA-run budget exhausted")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class SpecConversationManager:
    def __init__(self, store: SpecConversationStore, provider: SpecProvider):
        self.store = store
        self.provider = provider

    def create(self, *, message: str, project_id: str = "openroad-platform",
               design_id: str | None = None, design_context: Mapping[str, Any] | None = None,
               budgets: Mapping[str, Any] | None = None) -> dict[str, Any]:
        session_id = self.store.create(
            project_id=project_id, design_id=design_id,
            provider=self.provider.provider_name, model=self.provider.model, budgets=budgets,
        )
        return self.turn(session_id, message, design_context=design_context)

    def turn(self, session_id: str, message: str,
             *, design_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        message = _text(message, maximum=8000)
        if not message:
            raise ValueError("Spec turn is empty")
        session = self.store.get(session_id)
        messages = [{"role": item["role"], "content": item["content"]}
                    for item in session["turns"]]
        messages.append({"role": "user", "content": message})
        proposal = self.provider.propose(messages, session["state"],
                                         design_context=design_context)
        self.store.append_exchange(session_id, message, proposal,
                                   provider=self.provider.provider_name,
                                   model=self.provider.model)
        return self.store.get(session_id)

    def compile(self, session_id: str, *, rtl_path: str | Path, design_id: str,
                confirmed: bool) -> TaskSpec:
        """Removed v1 path: Spec sessions cannot submit direct RTL-to-GDS jobs.

        The method is retained only as an explicit migration failure for callers
        compiled against v1.0.  RTLScout-v2 is the sole RTL candidate producer.
        """
        raise RuntimeError(
            "Direct SpecConversation-to-ORFS compilation was removed in v2; "
            "materialize SpecIR and submit it through RTLScout-v2 instead"
        )


def _proposal_schema() -> dict[str, Any]:
    nullable_string = {"type": ["string", "null"]}
    properties = {
        "objective": {"type": "string"}, "functionality": {"type": "string"},
        "top": nullable_string, "clock": nullable_string, "reset": nullable_string,
        "target_platform": {"type": "string", "enum": sorted(ALLOWED_PLATFORMS)},
        "target_stage": {"type": "string", "enum": sorted(ALLOWED_STAGES)},
        "clock_period_ns": {"type": "number"},
        "core_utilization_pct": {"type": "number"},
        "place_density": {"type": "number"},
        "ports": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "direction": {"type": "string", "enum": ["input", "output", "inout"]},
            "width": {"type": ["integer", "null"]},
        }, "required": ["name", "direction", "width"], "additionalProperties": False}},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "clarification_questions": {"type": "array", "items": {"type": "string"}},
        "ready_for_execution": {"type": "boolean"},
    }
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _bounded_float(value: Any, minimum: float, maximum: float, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} is outside policy")
    return result


def _text(value: Any, *, maximum: int) -> str:
    result = " ".join(str(value or "").split())
    if len(result) > maximum:
        raise ValueError("Text field exceeds policy")
    return result


def _optional(value: Any, *, maximum: int) -> str | None:
    result = str(value or "").strip()
    if len(result) > maximum:
        raise ValueError("Text field exceeds policy")
    return result or None


def _identifier(value: Any) -> str | None:
    result = _optional(value, maximum=128)
    if result is not None and not re.fullmatch(r"[A-Za-z_]\w*", result):
        raise ValueError(f"Invalid HDL identifier: {result}")
    return result


def _strings(value: Any, *, maximum: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError("Expected a bounded string list")
    return tuple(_text(item, maximum=1000) for item in value if _text(item, maximum=1000))


def _ports(value: Any) -> tuple[PortSpec, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > 256:
        raise ValueError("ports must be a list of at most 256 entries")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError("Each port must be an object")
    result = tuple(PortSpec(
        name=str(item.get("name") or ""), direction=str(item.get("direction") or ""),
        width=item.get("width"),
    ) for item in value)
    for item in result:
        item.validate()
    if len({item.name for item in result}) != len(result):
        raise ValueError("ports must have unique names")
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
