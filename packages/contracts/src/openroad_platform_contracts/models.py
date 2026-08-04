from __future__ import annotations

import dataclasses
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RunStage(str, Enum):
    SYNTH = "synth"
    FLOORPLAN = "floorplan"
    PLACE = "place"
    CTS = "cts"
    ROUTE = "route"
    FINISH = "finish"


STAGE_ORDER = tuple(RunStage)


class RunStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ArtifactKind(str, Enum):
    RTL = "rtl"
    NETLIST = "netlist"
    ODB = "odb"
    DEF = "def"
    GDS = "gds"
    LOG = "log"
    REPORT = "report"
    METRICS = "metrics"
    OTHER = "other"


@dataclass(frozen=True)
class RunRequest:
    rtl_path: str
    top: str | None = None
    clock: str | None = None
    clock_period_ns: float = 10.0
    platform: str = "nangate45"
    target_stage: RunStage = RunStage.FINISH
    core_utilization_pct: float = 10.0
    place_density: float = 0.45
    stage_timeout_seconds: int = 3600
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    labels: dict[str, str] = field(default_factory=dict)

    def validate(self, *, require_rtl: bool = True) -> None:
        rtl = Path(self.rtl_path).expanduser()
        if require_rtl and not rtl.is_file():
            raise ValueError(f"RTL file does not exist: {rtl}")
        if self.top is not None and not re.fullmatch(r"[A-Za-z_]\w*", self.top):
            raise ValueError(f"Invalid top module: {self.top}")
        if self.clock is not None and not re.fullmatch(r"[A-Za-z_]\w*", self.clock):
            raise ValueError(f"Invalid clock port: {self.clock}")
        if self.clock_period_ns <= 0:
            raise ValueError("clock_period_ns must be positive")
        if not 0 < self.core_utilization_pct < 100:
            raise ValueError("core_utilization_pct must be between 0 and 100")
        if not 0 < self.place_density <= 1:
            raise ValueError("place_density must be between 0 and 1")
        if self.stage_timeout_seconds <= 0:
            raise ValueError("stage_timeout_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        return _to_primitive(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunRequest:
        payload = dict(value)
        payload["target_stage"] = RunStage(payload.get("target_stage", RunStage.FINISH))
        return cls(**payload)


@dataclass(frozen=True)
class ExecutionPlan:
    run_id: str
    design: str
    clock: str | None
    workdir: str
    flow_home: str
    config_path: str
    stages: tuple[RunStage, ...]
    request: RunRequest


@dataclass(frozen=True)
class StageResult:
    stage: RunStage
    status: RunStatus
    returncode: int
    seconds: float
    message: str | None = None


@dataclass(frozen=True)
class Artifact:
    kind: ArtifactKind
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class Metric:
    name: str
    value: float | int | str | None
    unit: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    design: str
    workdir: str
    started_at: str
    finished_at: str
    stages: tuple[StageResult, ...]
    artifacts: tuple[Artifact, ...]
    milestones: dict[str, bool] = field(default_factory=dict)
    metrics: tuple[Metric, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def _to_primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {item.name: _to_primitive(getattr(value, item.name))
                for item in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_primitive(item) for item in value]
    return value
