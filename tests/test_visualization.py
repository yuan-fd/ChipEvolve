from openroad_platform_visualization import generate_svg, parse_ports_and_gates


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

