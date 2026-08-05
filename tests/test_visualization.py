import pytest

from openroad_platform_visualization import (
    generate_schematic_svg,
    generate_svg,
    parse_ports_and_gates,
    render_gds,
    render_gds_3d,
)


def test_dependency_free_circuit_overview_is_preserved():
    source = (
        "module top(a, b, y);\n"
        "input a; input b; output y;\n"
        "NAND2_X1 u1 (.A1(a), .A2(b), .ZN(y));\n"
        "endmodule\n"
    )
    data = parse_ports_and_gates(source)
    svg = generate_svg(data)
    assert data["name"] == "top"
    assert data["instances"] == 1
    assert "<svg" in svg and "top" in svg


def test_graphviz_schematic_contains_real_gate_connectivity():
    source = (
        "module top(a, b, y);\n"
        "input a; input b; output y; wire n1;\n"
        "NAND2_X1 u1 (.A1(a), .A2(b), .ZN(n1));\n"
        "INV_X1 u2 (.A(n1), .ZN(y));\n"
        "endmodule\n"
    )

    svg = generate_schematic_svg(source)

    assert "<svg" in svg
    assert "g_u1" in svg and "g_u2" in svg
    assert "edge" in svg


def test_klayout_renders_real_minimal_gds_in_2d_and_3d(tmp_path):
    pya = pytest.importorskip("pya")
    layout = pya.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    top.shapes(layout.layer(1, 0)).insert(pya.Box(0, 0, 10_000, 8_000))
    top.shapes(layout.layer(2, 0)).insert(pya.Box(2_000, 1_000, 8_000, 7_000))
    gds = tmp_path / "minimal.gds"
    layout.write(str(gds))
    two_d = tmp_path / "layout-2d.png"
    three_d = tmp_path / "layout-3d.png"

    render_gds(gds, output=two_d, dpi=80)
    render_gds_3d(gds, output=three_d, dpi=80)

    assert two_d.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert three_d.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert two_d.stat().st_size > 1000
    assert three_d.stat().st_size > 1000
