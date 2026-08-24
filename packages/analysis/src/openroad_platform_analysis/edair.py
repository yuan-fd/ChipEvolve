"""EDAIR v1: provenance-first interchange between EDA artifacts and AI agents.

EDAIR is intentionally an envelope, not a replacement for DEF/ODB/GDS/log
files.  The original files stay authoritative; every normalized object points
back to an immutable artifact digest and parser version.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


def artifact_ref(*, artifact_id: str, sha256: str, kind: str, parser: str,
                 parser_version: str, source_size_bytes: int | None = None) -> dict[str, Any]:
    if not artifact_id or not kind or not parser or not parser_version:
        raise ValueError("artifact identity and parser provenance are required")
    if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("artifact sha256 is invalid")
    if source_size_bytes is not None and (not isinstance(source_size_bytes, int) or source_size_bytes < 0):
        raise ValueError("source size is invalid")
    return {"artifact_id": artifact_id, "sha256": sha256, "kind": kind,
            "parser": parser, "parser_version": parser_version,
            "source_size_bytes": source_size_bytes}


def timing_ir(paths: Iterable[Mapping[str, Any]], *, source: Mapping[str, Any],
              truncated: bool = False) -> dict[str, Any]:
    """Normalize timing paths without inventing unavailable cell/net details."""
    ref = _ref(source); rows = []
    for index, path in enumerate(paths):
        name = str(path.get("path_id") or f"path-{index}")
        slack = _number_or_none(path.get("slack_ns"))
        if slack is None:
            raise ValueError("each timing path requires numeric slack_ns")
        points = path.get("points") or []
        if not isinstance(points, list):
            raise ValueError("timing points must be a list")
        rows.append({"path_id": name, "path_type": str(path.get("path_type") or "setup"),
                     "startpoint": _optional_text(path.get("startpoint")),
                     "endpoint": _optional_text(path.get("endpoint")), "slack_ns": slack,
                     "delay_ns": _number_or_none(path.get("delay_ns")),
                     "points": [_timing_point(item) for item in points],
                     "evidence": ref})
    return {"kind": "timing_ir", "schema_version": 1, "source": ref,
            "paths": rows, "truncated": bool(truncated)}


def physical_ir(*, instances: Iterable[Mapping[str, Any]], nets: Iterable[Mapping[str, Any]],
                violations: Iterable[Mapping[str, Any]], source: Mapping[str, Any],
                grid: Mapping[str, Any] | None = None, truncated: bool = False) -> dict[str, Any]:
    """Represent PnR facts as attributed objects, retaining unknown values."""
    ref = _ref(source)
    def instance(item: Mapping[str, Any]) -> dict[str, Any]:
        return {"name": _required_text(item, "name"), "cell_type": _optional_text(item.get("cell_type")),
                "x": _number_or_none(item.get("x")), "y": _number_or_none(item.get("y")),
                "width": _number_or_none(item.get("width")), "height": _number_or_none(item.get("height")),
                "orientation": _optional_text(item.get("orientation")), "evidence": ref}
    def net(item: Mapping[str, Any]) -> dict[str, Any]:
        return {"name": _required_text(item, "name"), "wirelength_um": _number_or_none(item.get("wirelength_um")),
                "fanout": _integer_or_none(item.get("fanout")), "evidence": ref}
    def violation(item: Mapping[str, Any]) -> dict[str, Any]:
        return {"rule": _required_text(item, "rule"), "severity": _optional_text(item.get("severity")),
                "x": _number_or_none(item.get("x")), "y": _number_or_none(item.get("y")),
                "layer": _optional_text(item.get("layer")), "evidence": ref}
    return {"kind": "physical_ir", "schema_version": 1, "source": ref,
            "instances": [instance(x) for x in instances], "nets": [net(x) for x in nets],
            "violations": [violation(x) for x in violations], "grid": dict(grid or {}),
            "truncated": bool(truncated)}


def build_edair(*, design: Mapping[str, Any] | None, run: Mapping[str, Any],
                timing: Mapping[str, Any] | None = None, physical: Mapping[str, Any] | None = None,
                raw_artifacts: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Join independently parsed views into one content-addressed EDAIR record."""
    if run.get("kind") != "run_evidence_ir":
        raise ValueError("run must be a RunEvidenceIR")
    if design is not None and design.get("kind") != "design_ir":
        raise ValueError("design must be a DesignIR")
    for value, kind in ((timing, "timing_ir"), (physical, "physical_ir")):
        if value is not None and value.get("kind") != kind:
            raise ValueError(f"expected {kind}")
    artifacts = [_ref(item) for item in raw_artifacts]
    payload = {"schema_version": 1, "kind": "edair", "design": dict(design) if design else None,
               "run": dict(run), "timing": dict(timing) if timing else None,
               "physical": dict(physical) if physical else None, "raw_artifacts": artifacts,
               "loss_policy": "raw artifacts remain authoritative; normalized views may be truncated explicitly"}
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def agent_evidence_view(edair: Mapping[str, Any], *, max_items: int = 32) -> dict[str, Any]:
    """Bound an agent context while exposing every omitted object's source ref."""
    if edair.get("kind") != "edair" or not isinstance(edair.get("fingerprint"), str):
        raise ValueError("expected EDAIR")
    if not 1 <= max_items <= 256:
        raise ValueError("max_items outside policy")
    facts = []
    for path in (edair.get("timing") or {}).get("paths", []):
        if len(facts) >= max_items: break
        facts.append({"kind": "timing_path", "claim": f"{path['path_type']} {path['path_id']} slack={path['slack_ns']}ns",
                      "evidence": path["evidence"]})
    for violation in (edair.get("physical") or {}).get("violations", []):
        if len(facts) >= max_items: break
        facts.append({"kind": "physical_violation", "claim": f"{violation['rule']} at ({violation['x']},{violation['y']})",
                      "evidence": violation["evidence"]})
    return {"kind": "edair_agent_view", "edair_fingerprint": edair["fingerprint"], "facts": facts,
            "truncated": len(facts) >= max_items, "raw_artifact_refs": edair.get("raw_artifacts", []),
            "execution_allowed": False}


def _ref(value: Mapping[str, Any]) -> dict[str, Any]:
    return artifact_ref(artifact_id=str(value.get("artifact_id") or value.get("source_artifact_id") or "unknown"),
                        sha256=str(value.get("sha256") or value.get("source_sha256") or ""),
                        kind=str(value.get("kind") or "report"), parser=str(value.get("parser") or "unknown-parser"),
                        parser_version=str(value.get("parser_version") or "unknown"),
                        source_size_bytes=value.get("source_size_bytes"))

def _timing_point(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"instance": _optional_text(value.get("instance")), "pin": _optional_text(value.get("pin")),
            "net": _optional_text(value.get("net")), "increment_ns": _number_or_none(value.get("increment_ns"))}
def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = str(value.get(key) or "").strip()
    if not result: raise ValueError(f"{key} is required")
    return result
def _optional_text(value: Any) -> str | None: return str(value).strip() if value is not None and str(value).strip() else None
def _number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
def _integer_or_none(value: Any) -> int | None: return value if isinstance(value, int) and not isinstance(value, bool) else None
def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
