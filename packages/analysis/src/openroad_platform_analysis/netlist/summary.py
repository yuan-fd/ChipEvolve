from __future__ import annotations

from collections import Counter
from pathlib import Path

from .analyzer import NetlistAnalyzer
from .parser import parse_verilog_netlist


def summarize_netlist(path: str | Path) -> dict:
    netlist = parse_verilog_netlist(path)
    analyzer = NetlistAnalyzer(netlist, max_truth_table_vars=10)
    counts = Counter(instance.cell_type.upper() for instance in netlist.instances)
    max_depth = 0
    max_path = None
    for source in netlist.inputs:
        for destination in netlist.outputs:
            try:
                depth, path_result = analyzer.max_logic_depth(source, destination)
            except ValueError:
                continue
            if depth > max_depth:
                max_depth = depth
                max_path = {
                    "source": source,
                    "destination": destination,
                    "signals": path_result.nodes,
                    "instances": path_result.instances,
                }
    return {
        "module": netlist.module_name,
        "inputs": sorted(netlist.inputs),
        "outputs": sorted(netlist.outputs),
        "instance_count": len(netlist.instances),
        "cell_types": dict(sorted(counts.items())),
        "dff_count": sum(1 for instance in netlist.instances if instance.is_dff),
        "max_combinational_depth": max_depth,
        "max_depth_path": max_path,
    }

