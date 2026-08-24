"""Bounded, provenance-bearing DesignIR and Agent evidence-card projections."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .netlist import parse_verilog_netlist


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _width(value: tuple[int, int] | None) -> int | None:
    return None if value is None else abs(value[0] - value[1]) + 1


def build_design_ir(netlist_path: str | Path, *, max_instances: int = 20_000,
                    max_connections_per_instance: int = 32) -> dict[str, Any]:
    """Project a synthesized netlist into JSON-safe, bounded structured data.

    It does not infer timing, connectivity or cell semantics beyond what the
    parser observes. Truncation is explicit so an agent never mistakes a
    summary for the full physical design.
    """
    source = Path(netlist_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(source)
    if not 1 <= max_instances <= 100_000 or not 1 <= max_connections_per_instance <= 256:
        raise ValueError("DesignIR bounds are outside policy")
    netlist = parse_verilog_netlist(source)
    instances = []
    for instance in netlist.instances[:max_instances]:
        connections = dict(list(instance.named_connections.items())[:max_connections_per_instance])
        instances.append({
            "name": instance.name, "cell_type": instance.cell_type,
            "output": instance.output, "inputs": list(instance.inputs[:max_connections_per_instance]),
            "named_connections": connections, "is_dff": instance.is_dff,
            "connections_truncated": len(instance.named_connections) > len(connections)
                or len(instance.inputs) > max_connections_per_instance,
        })
    return {
        "schema_version": 1, "kind": "design_ir", "source": {
            "artifact_kind": "netlist", "path_name": source.name, "sha256": _sha256(source),
            "size_bytes": source.stat().st_size,
        },
        "module": netlist.module_name,
        "ports": [{"name": name, "direction": "input", "width": _width(width)}
                  for name, width in netlist.inputs.items()] +
                 [{"name": name, "direction": "output", "width": _width(width)}
                  for name, width in netlist.outputs.items()],
        "wires": [{"name": name, "width": _width(width)} for name, width in netlist.wires.items()],
        "instances": instances,
        "truncation": {"instances": len(netlist.instances) > len(instances),
                       "total_instances": len(netlist.instances),
                       "max_instances": max_instances,
                       "max_connections_per_instance": max_connections_per_instance},
    }


def evidence_cards_from_design_ir(design_ir: Mapping[str, Any], *, limit: int = 24) -> list[dict[str, Any]]:
    """Create bounded factual cards for agents; no recommendation is emitted."""
    if design_ir.get("kind") != "design_ir" or not isinstance(design_ir.get("source"), Mapping):
        raise ValueError("expected a DesignIR payload")
    if not 1 <= limit <= 100:
        raise ValueError("evidence-card limit is outside policy")
    source = design_ir["source"]
    ref = f"artifact:netlist:{source['sha256']}"
    cards = [{"kind": "design_summary", "claim": (
        f"module {design_ir.get('module')} has {len(design_ir.get('ports', []))} ports and "
        f"{design_ir.get('truncation', {}).get('total_instances', 0)} observed instances"),
        "evidence_ref": ref, "evidence_sha256": source["sha256"], "action_eligible": False}]
    for item in design_ir.get("instances", [])[:max(0, limit - 1)]:
        cards.append({"kind": "instance_fact", "claim": (
            f"instance {item['name']} is cell {item['cell_type']} with output {item.get('output') or 'unknown'}"),
            "evidence_ref": ref, "evidence_sha256": source["sha256"], "action_eligible": False})
    return cards[:limit]


def design_ir_json(design_ir: Mapping[str, Any]) -> str:
    """Canonical serialization for artifact IDs/caches."""
    return json.dumps(dict(design_ir), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
