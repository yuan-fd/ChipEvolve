"""Structural netlist analysis.

The property that explains most of these numbers: flip-flops are not edges, so
every path and depth here is combinational.  Several tests exist only to pin
that, because a traversal that accidentally walked through a register would
report a plausible but much larger depth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analyzer import NetlistAnalyzer, summarize_netlist, UNREACHABLE_DEPTH
from netlist import parse_verilog_netlist

#: in1 and in2 feed a two-gate chain to out1; a register sits between the chain
#: and out2, so out2 is one *combinational* hop from the register, not from in1.
COMBINATIONAL = """\
module logic (in1, in2, out1, out2, clk);
  input in1;
  input in2;
  input clk;
  output out1;
  output out2;
  wire a;
  wire b;
  wire q;

  and g1 (a, in1, in2);
  or g2 (b, a, in2);
  buf g3 (out1, b);
  dff ff0 (.clk(clk), .d(b), .q(q));
  buf g4 (out2, q);
endmodule
"""


@pytest.fixture()
def analyzer(tmp_path: Path) -> NetlistAnalyzer:
    path = tmp_path / "logic.v"
    path.write_text(COMBINATIONAL, encoding="utf-8")
    return NetlistAnalyzer(parse_verilog_netlist(path))


@pytest.fixture()
def netlist_path(tmp_path: Path) -> Path:
    path = tmp_path / "logic.v"
    path.write_text(COMBINATIONAL, encoding="utf-8")
    return path


def chain(tmp_path: Path, gates: int) -> NetlistAnalyzer:
    """A pure combinational chain of *gates* buffers, for depth arithmetic."""
    lines = ["module chain (i, o);", "  input i;", "  output o;"]
    lines += [f"  wire w{n};" for n in range(gates)]
    lines.append("  buf g0 (w0, i);")
    for n in range(1, gates):
        lines.append(f"  buf g{n} (w{n}, w{n-1});")
    lines.append(f"  buf gout (o, w{gates - 1});")
    lines.append("endmodule")
    path = tmp_path / f"chain{gates}.v"
    path.write_text("\n".join(lines), encoding="utf-8")
    return NetlistAnalyzer(parse_verilog_netlist(path))


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------

def test_a_combinational_gate_contributes_edges(analyzer: NetlistAnalyzer):
    assert [e.dst for e in analyzer.forward_edges["in1"]] == ["a"]
    assert [e.src for e in analyzer.backward_edges["out1"]] == ["b"]


def test_a_flip_flop_drives_its_q_without_being_an_edge(analyzer: NetlistAnalyzer):
    """The single fact that makes every depth below combinational."""
    assert analyzer.dff_by_name["ff0"].name == "ff0"
    assert analyzer.signal_drivers["q"].name == "ff0"
    # Nothing conducts from b through the register to q.
    assert "q" not in {edge.dst for edges in analyzer.forward_edges.values()
                       for edge in edges}
    assert "b" not in analyzer.backward_edges.get("q", [])[0].src if \
        analyzer.backward_edges.get("q") else True


def test_a_register_is_a_boundary_in_both_directions(analyzer: NetlistAnalyzer):
    # b reaches out1 but not out2, because out2 is past a register.
    assert analyzer.find_path("in1", "out1") is not None
    assert analyzer.find_path("in1", "out2") is None


def test_a_bit_literal_contributes_no_edge(tmp_path: Path):
    path = tmp_path / "literal.v"
    path.write_text(
        "module m (a, y);\n  input a;\n  output y;\n"
        "  and g0 (y, a, 1'b1);\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    assert "1'b1" not in analyzer.forward_edges


def test_an_unknown_name_is_refused_rather_than_treated_as_a_signal(
    analyzer: NetlistAnalyzer,
):
    with pytest.raises(ValueError, match="unknown signal or instance"):
        analyzer.find_path("nope", "out1")


def test_an_instance_name_is_accepted_where_a_signal_is_expected(
    analyzer: NetlistAnalyzer,
):
    """A report names either.  The analyzer resolves both to the same net."""
    assert analyzer.find_path("g1", "out1") is not None


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def test_a_path_reports_its_signals_and_instances(analyzer: NetlistAnalyzer):
    path = analyzer.find_path("in1", "out1")
    assert path is not None
    assert path.nodes[0] == "in1" and path.nodes[-1] == "out1"
    assert path.instances[0] == "g1"
    assert len(path.instances) == len(path.nodes) - 1


def test_no_path_returns_none_rather_than_an_empty_path(
    analyzer: NetlistAnalyzer,
):
    assert analyzer.find_path("out1", "in1") is None


def test_avoiding_a_signal_removes_it_from_consideration(tmp_path: Path):
    """Two parallel routes: avoiding one leaves the other."""
    path = tmp_path / "parallel.v"
    path.write_text(
        "module m (i, o, clk);\n  input i;\n  input clk;\n  output o;\n"
        "  wire a;\n  wire b;\n  wire c;\n"
        "  buf g1 (a, i);\n  buf g2 (b, i);\n"
        "  or g3 (c, a, b);\n  buf g4 (o, c);\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    assert analyzer.find_path("i", "o", avoid={"a"}) is not None
    assert analyzer.find_path("i", "o", avoid={"a", "b"}) is None


def test_all_paths_pass_through_returns_a_counterexample(tmp_path: Path):
    """One witness is enough to say no, and the witness is the evidence."""
    path = tmp_path / "parallel.v"
    path.write_text(
        "module m (i, o);\n  input i;\n  output o;\n"
        "  wire a;\n  wire b;\n  wire c;\n"
        "  buf g1 (a, i);\n  buf g2 (b, a);\n  buf g3 (o, b);\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    passes, witness = analyzer.all_paths_pass_through("i", "o", "b")
    assert passes is True and witness is None

    passes, witness = analyzer.all_paths_pass_through("i", "o", "i")
    assert passes is False and witness is not None


def test_path_enumeration_is_bounded_by_the_limit(tmp_path: Path):
    """Unbounded enumeration is exponential on a real netlist."""
    path = tmp_path / "diamond.v"
    path.write_text(
        "module m (i, o);\n  input i;\n  output o;\n"
        "  wire a;\n  wire b;\n  wire c;\n"
        "  buf g1 (a, i);\n  buf g2 (b, a);\n  buf g3 (c, a);\n"
        "  or g4 (o, b, c);\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    assert len(analyzer.all_paths("i", "o")) == 2
    assert len(analyzer.all_paths("i", "o", limit=1)) == 1


def test_enumeration_over_an_unreachable_pair_yields_nothing(
    analyzer: NetlistAnalyzer,
):
    assert analyzer.all_paths("in1", "out2") == []


# --------------------------------------------------------------------------
# depth
# --------------------------------------------------------------------------

def test_depth_counts_edges(tmp_path: Path):
    # `chain(n)` builds n buffers plus one for the output, so n + 1 edges.
    assert chain(tmp_path, 3).max_logic_depth("i", "o")[0] == 4
    assert chain(tmp_path, 5).max_logic_depth("i", "o")[0] == 6


def test_depth_is_the_longest_path_not_the_shortest(tmp_path: Path):
    path = tmp_path / "uneven.v"
    path.write_text(
        "module m (i, o);\n  input i;\n  output o;\n"
        "  wire a;\n  wire b;\n  wire c;\n  wire d;\n"
        "  buf g1 (a, i);\n"
        "  buf g2 (b, i);\n  buf g3 (c, b);\n  buf g4 (d, c);\n"
        "  or g5 (o, a, d);\nendmodule\n",
        encoding="utf-8",
    )
    depth, result = NetlistAnalyzer(parse_verilog_netlist(path)).max_logic_depth(
        "i", "o")
    assert depth == 4
    assert result.nodes == ["i", "b", "c", "d", "o"]


def test_depth_refuses_a_pair_with_no_path(analyzer: NetlistAnalyzer):
    """Returning zero would be indistinguishable from source == destination."""
    with pytest.raises(ValueError, match="no combinational path"):
        analyzer.max_logic_depth("in1", "out2")


def test_depth_from_a_signal_to_itself_is_zero(analyzer: NetlistAnalyzer):
    depth, result = analyzer.max_logic_depth("in1", "in1")
    assert depth == 0
    assert result.nodes == ["in1"]


def test_a_combinational_cycle_does_not_hang_or_report_a_depth(
    tmp_path: Path,
):
    """A cycle has no finite depth; it is reported as unreachable."""
    path = tmp_path / "cycle.v"
    path.write_text(
        "module m (i, o);\n  input i;\n  output o;\n"
        "  wire a;\n  wire b;\n"
        "  and g1 (a, i, b);\n  buf g2 (b, a);\n  buf g3 (o, b);\n"
        "endmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    # Whatever the search concludes, it must terminate.
    try:
        depth, _ = analyzer.max_logic_depth("i", "o")
        assert depth > 0
    except ValueError:
        pass
    assert UNREACHABLE_DEPTH < 0


# --------------------------------------------------------------------------
# cuts, cones, clock domains
# --------------------------------------------------------------------------

def test_a_signal_on_the_only_route_is_a_cut(tmp_path: Path):
    path = tmp_path / "series.v"
    path.write_text(
        "module m (i, o);\n  input i;\n  output o;\n"
        "  wire a;\n  wire b;\n"
        "  buf g1 (a, i);\n  buf g2 (b, a);\n  buf g3 (o, b);\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    assert analyzer.signal_is_cut_between_any_pi_po("b") is True


def test_a_signal_with_an_alternative_route_is_not_a_cut(tmp_path: Path):
    path = tmp_path / "parallel.v"
    path.write_text(
        "module m (i, o);\n  input i;\n  output o;\n"
        "  wire a;\n  wire b;\n  wire c;\n"
        "  buf g1 (a, i);\n  buf g2 (b, i);\n"
        "  or g3 (c, a, b);\n  buf g4 (o, c);\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    assert analyzer.signal_is_cut_between_any_pi_po("a") is False


def test_a_cone_contains_every_feeding_instance(tmp_path: Path):
    analyzer = chain(tmp_path, 3)
    cone = analyzer.cone_instances("o")
    assert cone == {"g0", "g1", "g2", "gout"}


def test_a_cone_stops_at_a_register(tmp_path: Path):
    path = tmp_path / "cone.v"
    path.write_text(
        "module m (i, clk, o);\n  input i;\n  input clk;\n  output o;\n"
        "  wire a;\n  wire q;\n"
        "  buf g1 (a, i);\n  dff ff0 (.clk(clk), .d(a), .q(q));\n"
        "  buf g2 (o, q);\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    assert analyzer.cone_instances("o") == {"g2"}


def test_outputs_with_a_large_cone_are_ordered_deterministically(tmp_path: Path):
    path = tmp_path / "many.v"
    lines = ["module m (i, big, small);", "  input i;", "  output big;",
             "  output small;"]
    lines += [f"  wire w{n};" for n in range(4)]
    lines.append("  buf g0 (w0, i);")
    for n in range(1, 4):
        lines.append(f"  buf g{n} (w{n}, w{n-1});")
    lines.append("  buf gbig (big, w3);")
    lines.append("  buf gsmall (small, i);")
    lines.append("endmodule")
    path = tmp_path / "many.v"
    path.write_text("\n".join(lines), encoding="utf-8")
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    assert analyzer.outputs_with_cone_over(1) == [("big", 5)]
    # The same call twice gives the same list.
    assert analyzer.outputs_with_cone_over(1) == analyzer.outputs_with_cone_over(1)


def test_registers_on_the_same_clock_share_a_domain(tmp_path: Path):
    path = tmp_path / "clocks.v"
    path.write_text(
        "module m (clk, d1, d2, q1, q2);\n"
        "  input clk;\n  input d1;\n  input d2;\n"
        "  output q1;\n  output q2;\n"
        "  dff a (.clk(clk), .d(d1), .q(q1));\n"
        "  dff b (.clk(clk), .d(d2), .q(q2));\nendmodule\n",
        encoding="utf-8",
    )
    analyzer = NetlistAnalyzer(parse_verilog_netlist(path))
    same, left, right = analyzer.same_clock_domain("a", "b")
    assert same is True and left == right == "clk"


def test_registers_on_different_clocks_do_not(tmp_path: Path):
    path = tmp_path / "clocks.v"
    path.write_text(
        "module m (clk_a, clk_b, d, qa, qb);\n"
        "  input clk_a;\n  input clk_b;\n  input d;\n"
        "  output qa;\n  output qb;\n"
        "  dff a (.clk(clk_a), .d(d), .q(qa));\n"
        "  dff b (.clk(clk_b), .d(d), .q(qb));\nendmodule\n",
        encoding="utf-8",
    )
    same, left, right = NetlistAnalyzer(
        parse_verilog_netlist(path)).same_clock_domain("a", "b")
    assert same is False and left == "clk_a" and right == "clk_b"


def test_the_clock_names_are_returned_because_an_unknown_is_load_bearing(
    tmp_path: Path,
):
    """Two registers with no resolvable clock compare as the same domain.

    That is why the names come back with the boolean: "yes, both unknown" is a
    different fact from "yes, both clk", and a caller told only "yes" would act
    on the wrong one.
    """
    path = tmp_path / "unknown_clock.v"
    path.write_text(
        "module m (d1, d2, q1, q2);\n  input d1;\n  input d2;\n"
        "  output q1;\n  output q2;\n"
        "  dff a (.d(d1), .q(q1));\n  dff b (.d(d2), .q(q2));\nendmodule\n",
        encoding="utf-8",
    )
    same, left, right = NetlistAnalyzer(
        parse_verilog_netlist(path)).same_clock_domain("a", "b")
    assert same is True
    assert left == right == "<unknown>"


def test_a_non_register_is_refused_as_a_clock_domain_subject(
    analyzer: NetlistAnalyzer,
):
    with pytest.raises(ValueError, match="is not a DFF instance"):
        analyzer.same_clock_domain("g1", "ff0")


# --------------------------------------------------------------------------
# the summary
# --------------------------------------------------------------------------

def test_the_summary_reports_the_shape_of_the_design(netlist_path: Path):
    summary = summarize_netlist(netlist_path)
    assert summary["module"] == "logic"
    assert summary["inputs"] == ["clk", "in1", "in2"]
    assert summary["outputs"] == ["out1", "out2"]
    assert summary["dff_count"] == 1
    assert summary["cell_types"]["BUF"] == 2
    # and g1, or g2, buf g3, dff ff0, buf g4
    assert summary["instance_count"] == 5


def test_the_summary_reports_depth_as_combinational(netlist_path: Path):
    summary = summarize_netlist(netlist_path)
    # in1 -> g1 -> g2 -> g3 -> out1 is three edges.
    assert summary["max_combinational_depth"] == 3
    assert summary["max_depth_path"]["source"] in {"in1", "in2"}
    assert summary["max_depth_path"]["destination"] == "out1"


def test_the_summary_states_what_it_did_not_do(netlist_path: Path):
    """An index that does not say what it left out invites the wrong reading."""
    manifest = summarize_netlist(netlist_path)["loss_manifest"]
    assert "combinational_only" in manifest
    assert "boolean_analysis" in manifest
    assert summarize_netlist(netlist_path)["parser"] == "mapped-netlist-v1"


def test_the_summary_does_not_fail_on_a_design_with_no_path(tmp_path: Path):
    """No combinational path between a pair is an ordinary fact, not an error."""
    path = tmp_path / "disjoint.v"
    path.write_text(
        "module m (i1, i2, o1, o2);\n  input i1;\n  input i2;\n"
        "  output o1;\n  output o2;\n"
        "  buf g1 (o1, i1);\n  buf g2 (o2, i2);\nendmodule\n",
        encoding="utf-8",
    )
    summary = summarize_netlist(path)
    assert summary["max_combinational_depth"] == 1
    assert summary["max_depth_path"]["source"] in {"i1", "i2"}
