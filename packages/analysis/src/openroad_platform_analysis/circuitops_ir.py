"""Low-dependency reader for CircuitOps relational/LPG intermediate tables.

CircuitOps (Liang et al., ICCAD 2023) keeps detailed EDA facts in normalized
CSV tables and constructs a labelled property graph on top.  Importing its
full Python package would pull graph-tool/DGL/Torch into the web process, so
this adapter consumes the stable table contract directly.  It preserves table
digests and supports bounded queries; it never compresses an entire design
into an LLM prompt.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


TABLES = (
    "design_properties.csv", "libcell_properties.csv", "pin_properties.csv",
    "cell_properties.csv", "net_properties.csv", "pin_pin_edge.csv",
    "cell_pin_edge.csv", "net_pin_edge.csv", "cell_net_edge.csv",
    "cell_cell_edge.csv",
)


def export_netlist_to_circuitops(netlist_path: str | Path, output_root: str | Path,
                                 *, design_name: str | None = None,
                                 platform: str = "unknown") -> dict[str, Any]:
    """Export a parsed gate netlist to the low-loss CircuitOps table contract.

    This is the deterministic digital-front-end exporter.  It intentionally
    does not invent placement, timing, library, or physical properties: those
    tables are emitted with only values observed from the netlist (and empty
    property tables are omitted).  The original netlist remains authoritative
    and its SHA is recorded in ``design_properties.csv``.  A later OpenDB/DEF
    exporter can append physical columns without changing the relation schema.
    """
    from .netlist import parse_verilog_netlist

    source = Path(netlist_path).expanduser().resolve()
    destination = Path(output_root).expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(source)
    destination.mkdir(parents=True, exist_ok=True)
    netlist = parse_verilog_netlist(source)
    name = design_name or netlist.module_name
    digest = _sha256(source)

    cells: dict[str, str] = {}
    pins: list[tuple[str, str, str, str]] = []
    nets: dict[str, set[str]] = {}
    for instance in netlist.instances:
        cells[instance.name] = instance.cell_type
        connections = dict(instance.named_connections)
        if not connections:
            signals = ([instance.output] if instance.output else []) + list(instance.inputs)
            connections = {f"p{index}": signal for index, signal in enumerate(signals)}
        for pin, signal in connections.items():
            signal = str(signal).strip()
            if not signal:
                continue
            direction = "output" if signal == instance.output else "input"
            pins.append((f"{instance.name}.{pin}", instance.name, signal, direction))
            nets.setdefault(signal, set()).add(instance.name)
    for port in list(netlist.inputs) + list(netlist.outputs):
        nets.setdefault(port, set()).add(f"port:{port}")

    rows: dict[str, list[list[str]]] = {
        "design_properties.csv": [["design_name", "module", "platform", "source_sha256", "source_size_bytes"],
                                  [name, netlist.module_name, platform, digest, str(source.stat().st_size)]],
        # No library database is available from a plain netlist.  Keep the
        # table schema present with an explicit empty body instead of
        # pretending that cell area/delay properties were observed.
        "libcell_properties.csv": [["libcell_name"]],
        "cell_properties.csv": [["cell_name", "libcell_name"]] + [[key, value] for key, value in sorted(cells.items())],
        "pin_properties.csv": [["pin_name", "cell_name", "net_name", "direction"]] + [list(item) for item in pins],
        "net_properties.csv": [["net_name", "fanout"]] + [[key, str(len(value))] for key, value in sorted(nets.items())],
        "cell_pin_edge.csv": [["src", "tar"]] + [[cell, pin] for pin, cell, _, _ in pins],
        "net_pin_edge.csv": [["src", "tar"]] + [[net, pin] for pin, _, net, _ in pins],
        "cell_net_edge.csv": [["src", "tar"]] + [[cell, net] for pin, cell, net, _ in pins],
        "pin_pin_edge.csv": [["src", "tar", "cell_name"]],
        "cell_cell_edge.csv": [["src", "tar"]],
    }
    for table, table_rows in rows.items():
        with (destination / table).open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(table_rows)
    # The exporter is itself provenance-bearing and can be indexed immediately.
    index = circuitops_lpg_ir(destination, required=False)
    manifest = {"kind": "circuitops_export_manifest", "schema_version": 1,
                "source_netlist": {"path_name": source.name, "sha256": digest},
                "design_name": name, "platform": platform, "index": index}
    (destination / "export_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return manifest


def circuitops_lpg_ir(root: str | Path, *, required: bool = True) -> dict[str, Any]:
    """Index a CircuitOps IR directory without loading its optional ML stack."""
    directory = Path(root).expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    tables = []
    for name in TABLES:
        path = directory / name
        if not path.is_file():
            if required:
                raise FileNotFoundError(f"CircuitOps table missing: {name}")
            continue
        with path.open("rb") as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.reader(stream); header = next(reader, [])
            count = sum(1 for _ in reader)
        tables.append({"name": name, "sha256": digest, "size_bytes": path.stat().st_size,
                       "columns": header, "row_count": count,
                       "role": "edge" if name.endswith("_edge.csv") else "property"})
    if not tables:
        raise ValueError("no CircuitOps tables found")
    return {"kind": "circuitops_lpg_ir", "schema_version": 1, "directory_name": directory.name,
            "tables": tables,
            "graph_model": {"nodes": ["pin", "cell", "net"],
                            "edges": ["pin_pin", "cell_pin", "net_pin", "cell_net", "cell_cell"]},
            "query_contract": "request_table_rows(table, columns, equals, limit); table hashes remain authoritative",
            "loss_policy": "no rows are summarized or discarded by indexing; queries are bounded explicitly"}


def request_table_rows(index: Mapping[str, Any], root: str | Path, *, table: str,
                       columns: list[str] | None = None, equals: Mapping[str, str] | None = None,
                       limit: int = 128) -> dict[str, Any]:
    """Return a hash-checked bounded relational excerpt for an agent/tool call."""
    if index.get("kind") != "circuitops_lpg_ir" or not 1 <= limit <= 4096:
        raise ValueError("invalid CircuitOps index or limit")
    meta = next((item for item in index.get("tables", []) if item.get("name") == table), None)
    if not isinstance(meta, Mapping):
        raise KeyError(table)
    path = Path(root).expanduser().resolve() / table
    if not path.is_file() or _sha256(path) != meta.get("sha256"):
        raise ValueError("CircuitOps table is missing or changed after indexing")
    filters = {str(k): str(v) for k, v in (equals or {}).items()}
    requested = list(columns or meta.get("columns") or [])
    if not requested or any(name not in meta.get("columns", []) for name in requested):
        raise ValueError("requested columns are not present in the indexed table")
    rows = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if all(row.get(key) == value for key, value in filters.items()):
                rows.append({key: row.get(key) for key in requested})
                if len(rows) >= limit:
                    break
    return {"kind": "circuitops_table_excerpt", "table": table, "table_sha256": meta["sha256"],
            "columns": requested, "equals": filters, "rows": rows, "limit": limit,
            "truncated": len(rows) >= limit, "execution_allowed": False}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
