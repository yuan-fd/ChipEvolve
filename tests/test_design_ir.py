from __future__ import annotations

from openroad_platform_analysis import build_design_ir, evidence_cards_from_design_ir


def test_design_ir_preserves_netlist_provenance_and_marks_truncation(tmp_path):
    path = tmp_path / "tiny.v"
    path.write_text("""module tiny(a,y);
input [3:0] a; output y; wire n;
and u1(n,a[0],a[1]);
buf u2(y,n);
endmodule
""")
    ir = build_design_ir(path, max_instances=1)
    assert ir["source"]["sha256"] and ir["module"] == "tiny"
    assert ir["ports"][0]["width"] == 4
    assert ir["truncation"]["instances"] is True
    cards = evidence_cards_from_design_ir(ir, limit=2)
    assert cards[0]["action_eligible"] is False
    assert cards[0]["evidence_sha256"] == ir["source"]["sha256"]
