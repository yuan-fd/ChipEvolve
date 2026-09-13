"""Protected ORFS signoff evaluation.

Ported from the frozen v1 implementation, behaviour-preserving.  This is the
module that decides whether an ORFS run is admissible, so its gates are quoted
from that implementation rather than re-derived.

The gates, in full:

* every required final artifact exists and is non-empty
* all six stages produced parseable JSON (``signoff_complete``)
* the required terminal metrics parsed
* the run's time unit was **verified** -- an unverified unit means every timing
  number is of unknown scale, which is a gate failure and not a footnote
* setup slack is non-negative
* hold slack is non-negative when reported
* detailed-route DRC is exactly zero

``feasible`` means all of the above.  It does **not** mean the RTL is
functionally correct, and the evaluation says so in its own claim boundary so
that nobody has to remember it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from stage_json import extract_metrics_from_log_dir

#: ORFS writes these at the end of a completed flow.  Their presence is the
#: cheapest evidence that the flow actually finished rather than being reported
#: as finished.
REQUIRED_FINAL_ARTIFACTS = (
    "6_final.odb", "6_final.def", "6_final.gds", "6_final.v",
)

#: Metrics without which no QoR claim can be made.  A missing one is a gate
#: failure; it is never defaulted to zero.
REQUIRED_METRICS = ("instance_area_um2", "setup_wns_ns", "power_W", "drc_errors")

EVALUATION_SCHEMA_VERSION = 1

CLAIM_BOUNDARY = (
    "Feasible means required final artifacts and ORFS stage JSON exist, required "
    "terminal metrics parse, setup and reported hold timing pass, and DRC is zero; "
    "it does not prove RTL functional correctness or silicon signoff."
)


def sha256_file(path: Path) -> str:
    """The plugin's single digest implementation."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha(value: str) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value.lower()))


def _number(value: Any) -> float | int | None:
    """Only a finite real number is a measurement.  ``True`` is not 1 here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


def evaluate_orfs_run(
    *, log_dir: str | Path, result_dir: str | Path, platform: str, design: str,
    design_identity_sha256: str, effective_config_sha256: str, or_seed: int,
    source_kind: str, clock_period_ns: float | None = None,
    runtime_seconds: float | None = None,
    run_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score immutable ORFS evidence under hard signoff and metric gates.

    ``source_kind`` labels provenance only.  It never changes parsing, required
    artifacts, constraints or objective values -- otherwise a baseline arm could
    be scored by an easier rule than a candidate arm, which would make every
    comparison in the study meaningless.
    """
    if not isinstance(source_kind, str) or not source_kind.strip():
        raise ValueError("source_kind is required")
    for name, value in (("design_identity_sha256", design_identity_sha256),
                        ("effective_config_sha256", effective_config_sha256)):
        if not _valid_sha(value):
            raise ValueError(f"{name} must be a SHA-256 hex digest")
    if isinstance(or_seed, bool) or not isinstance(or_seed, int) or or_seed < 0:
        raise ValueError("or_seed must be a non-negative integer")

    logs = Path(log_dir).expanduser().resolve()
    results = Path(result_dir).expanduser().resolve()

    normalized = extract_metrics_from_log_dir(
        logs, design=design, platform=platform,
        clock_period_ns=clock_period_ns, expected_stage="finish",
    )

    artifacts: list[dict[str, Any]] = []
    missing_artifacts: list[str] = []
    for filename in REQUIRED_FINAL_ARTIFACTS:
        path = results / filename
        if not path.is_file() or path.stat().st_size <= 0:
            missing_artifacts.append(filename)
            continue
        artifacts.append({
            "name": filename, "path": str(path),
            "size_bytes": path.stat().st_size, "sha256": sha256_file(path),
        })

    finish = {
        **normalized["stages"]["route"]["metrics"],
        **normalized["stages"]["finish"]["metrics"],
    }
    metrics = {
        "area_um2": _number(finish.get("instance_area_um2")),
        "instance_area_um2": _number(finish.get("instance_area_um2")),
        "die_area_um2": _number(finish.get("die_area_um2")),
        "setup_wns_ns": _number(finish.get("setup_wns_ns")),
        "setup_tns_ns": _number(finish.get("setup_tns_ns")),
        "hold_wns_ns": _number(finish.get("hold_wns_ns")),
        "power_W": _number(finish.get("power_W")),
        "drc_errors": _number(finish.get("drc_errors")),
        "runtime_seconds": _number(runtime_seconds),
    }

    missing_metrics = [name for name in REQUIRED_METRICS if metrics[name] is None]

    gate_reasons: list[str] = []
    if missing_artifacts:
        gate_reasons.append("missing_final_artifacts")
    if not normalized["summary"]["signoff_complete"]:
        gate_reasons.append("incomplete_stage_json")
    if missing_metrics:
        gate_reasons.append("missing_required_metrics")
    if normalized["units"]["time"]["status"] != "verified":
        gate_reasons.append("unverified_time_unit")
    if metrics["setup_wns_ns"] is not None and metrics["setup_wns_ns"] < 0:
        gate_reasons.append("setup_timing_violation")
    if metrics["hold_wns_ns"] is not None and metrics["hold_wns_ns"] < 0:
        gate_reasons.append("hold_timing_violation")
    if metrics["drc_errors"] is not None and metrics["drc_errors"] != 0:
        gate_reasons.append("drc_violation")
    feasible = not gate_reasons

    identity = {
        "platform": platform, "design": design,
        "design_identity_sha256": design_identity_sha256,
        "effective_config_sha256": effective_config_sha256,
        "or_seed": or_seed, "source_kind": source_kind,
    }
    digest_input = {"identity": identity, "metrics": metrics, "artifacts": artifacts}
    evaluation_id = hashlib.sha256(json.dumps(
        digest_input, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()

    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "kind": "common-orfs-signoff-evaluation",
        "evaluation_id": evaluation_id,
        "identity": identity,
        "feasible": feasible,
        "gate": {
            "status": "passed" if feasible else "failed",
            "reasons": gate_reasons,
            "missing_artifacts": missing_artifacts,
            "missing_metrics": missing_metrics,
            "rules": {
                "required_final_artifacts": list(REQUIRED_FINAL_ARTIFACTS),
                "required_metrics": list(REQUIRED_METRICS),
                "setup_wns_ns": ">= 0",
                "hold_wns_ns": ">= 0 when reported",
                "drc_errors": "== 0",
                "time_unit": "verified",
            },
        },
        "metrics": metrics,
        "artifacts": artifacts,
        "normalized_stage_evidence": normalized,
        "run_metadata": dict(run_metadata or {}),
        "claim_boundary": CLAIM_BOUNDARY,
    }


def write_immutable_evaluation(
    path: str | Path, evaluation: Mapping[str, Any]
) -> Path:
    """Create the evaluation once.  An identical retry is idempotent; a
    different one is refused rather than overwritten.

    Evidence that can be silently rewritten is not evidence.
    """
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(evaluation), indent=2, ensure_ascii=False) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") == payload:
            return target
        raise FileExistsError(
            f"refusing to overwrite an immutable evaluation: {target}"
        )
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return target
