"""Agent runtime trace: structured, inspectable record of agent behaviour.

Mirrors the AgenticEDA picture: User goal -> Agent (plan/think/schedule/
reflect) -> Tool adapters -> Evaluator metrics -> State store / result.
Every step is recorded so the web dashboard can show *what the agent is
doing* instead of a black box.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STEP_KINDS = ("goal", "plan", "think", "tool_call", "evaluate", "reflect",
              "schedule", "result")


@dataclass
class TraceStep:
    kind: str                       # one of STEP_KINDS
    title: str
    detail: str = ""
    tool: str | None = None         # tool name for tool_call steps
    status: str = "ok"              # running | ok | failed
    metrics: dict[str, Any] | None = None   # evaluator metrics
    started_at: float = field(default_factory=time.time)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "detail": self.detail,
                "tool": self.tool, "status": self.status, "metrics": self.metrics,
                "started_at": self.started_at, "duration_ms": self.duration_ms}


@dataclass
class AgentTrace:
    trace_id: str
    goal: str
    agent_kind: str                  # spec-to-rtl | recommendation | batch-search
    steps: list[TraceStep] = field(default_factory=list)
    status: str = "running"          # running | done | failed
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)

    def add(self, kind: str, title: str, **kwargs) -> TraceStep:
        step = TraceStep(kind=kind, title=title, **kwargs)
        self.steps.append(step)
        return step

    def start_tool(self, tool: str, title: str) -> TraceStep:
        return self.add("tool_call", title, tool=tool, status="running")

    def finish_tool(self, step: TraceStep, *, ok: bool = True,
                    metrics: dict[str, Any] | None = None,
                    detail: str = "") -> None:
        step.status = "ok" if ok else "failed"
        step.metrics = metrics
        if detail:
            step.detail = detail
        step.duration_ms = int((time.time() - step.started_at) * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "goal": self.goal,
                "agent_kind": self.agent_kind, "status": self.status,
                "steps": [s.to_dict() for s in self.steps],
                "result": self.result, "created_at": self.created_at}


class AgentTraceStore:
    """SQLite-backed trace storage (var/public/agent-traces.db)."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS agent_traces_v1 (
                    trace_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )""")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def create(self, goal: str, agent_kind: str) -> AgentTrace:
        trace = AgentTrace(trace_id=f"trace-{uuid.uuid4().hex[:20]}",
                           goal=goal, agent_kind=agent_kind)
        self.save(trace)
        return trace

    def save(self, trace: AgentTrace) -> None:
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO agent_traces_v1 VALUES (?, ?, ?)",
                        (trace.trace_id,
                         json.dumps(trace.to_dict(), ensure_ascii=False),
                         trace.created_at))

    def get(self, trace_id: str) -> AgentTrace | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json FROM agent_traces_v1 "
                              "WHERE trace_id = ?", (trace_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        trace = AgentTrace(trace_id=data["trace_id"], goal=data["goal"],
                           agent_kind=data["agent_kind"],
                           status=data["status"], result=data["result"],
                           created_at=data["created_at"])
        trace.steps = [TraceStep(**step) for step in data["steps"]]
        return trace

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT payload_json FROM agent_traces_v1 "
                               "ORDER BY created_at DESC LIMIT ?",
                               (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]
