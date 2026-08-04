from __future__ import annotations

import json

from openroad_platform_analysis.netlist import summarize_netlist
from openroad_platform_analysis.parsers import cell_coords, stage_json


def test_stage_metrics_and_density_are_available_without_web_app(tmp_path):
    logs = tmp_path / "logs/nangate45/top/base"
    logs.mkdir(parents=True)
    (logs / "1_synth.json").write_text(json.dumps({
        "synth__design__instance__count": 13,
        "synth__design__instance__area": 39.9,
    }))
    metrics = stage_json.extract_metrics(
        tmp_path, "nangate45", "top", expected_stage="synth"
    )
    assert metrics["stages"]["synth"]["metrics"]["instance_count"] == 13

    result_dir = tmp_path / "results/nangate45/top/base"
    result_dir.mkdir(parents=True)
    design = result_dir / "3_place.def"
    design.write_text(
        "UNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 10000 10000 ) ;\n"
        "COMPONENTS 1 ;\n- u1 NAND2X1 + PLACED ( 1000 2000 ) N ;\nEND COMPONENTS\n"
    )
    density = cell_coords.analyze(def_path=design, grid=10)
    assert density["available"] is True
    assert density["method"] == "count"


def test_netlist_summary_migrated_from_contest_engine(tmp_path):
    netlist = tmp_path / "top.v"
    netlist.write_text(
        "module top(a, b, y);\n"
        "input a, b; output y; wire n1;\n"
        "and g1(n1, a, b);\n"
        "not g2(y, n1);\n"
        "endmodule\n"
    )
    summary = summarize_netlist(netlist)
    assert summary["instance_count"] == 2
    assert summary["max_combinational_depth"] == 2
    assert summary["cell_types"] == {"AND": 1, "NOT": 1}


def test_netlist_summary_understands_yosys_mux_cells(tmp_path):
    netlist = tmp_path / "mux.v"
    netlist.write_text(
        "module mux(a, b, s, y);\n"
        "input a, b, s; output y;\n"
        "\\$_MUX_ u0 (.A(a), .B(b), .S(s), .Y(y));\n"
        "endmodule\n"
    )

    summary = summarize_netlist(netlist)

    assert summary["cell_types"] == {"MUX": 1}
    assert summary["max_combinational_depth"] == 1
