"""The mapped-netlist reader.

Two conventions in here are fragile enough that they are pinned by tests rather
than trusted: positional pin order, and which wires count as constants.  Both
were recorded behaviour in v1, and both silently produce a wrong graph if they
drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netlist import (
    CONSTANT_ALIASES,
    Instance,
    Netlist,
    parse_verilog_netlist,
    YOSYS_PRIMITIVE_CELL_TYPES,
)

#: A mapped netlist in the shape a synthesis tool emits: primitives, a flip-flop
#: with named pins, a positional buffer, a continuous assignment, and a comment.
MAPPED = """\
/* a block comment
   spanning lines */
module counter (clk, rst_n, q, done);
  input clk;
  input rst_n;
  output [3:0] q;
  output done;
  wire [3:0] q;
  wire n1;
  wire one_;
  wire [3:0] zero_;
  wire [7:0] bus;

  $_AND_ g1 (.A(clk), .B(rst_n), .Y(n1));
  $_DFF_P_ ff0 (.C(clk), .D(n1), .Q(q[0]));
  buf b1 (done, n1);
  assign n1 = clk & rst_n;  // trailing comment
endmodule
"""


@pytest.fixture()
def mapped(tmp_path: Path) -> Path:
    path = tmp_path / "6_final.v"
    path.write_text(MAPPED, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# declarations
# --------------------------------------------------------------------------

def test_the_module_and_its_header_ports_are_read(mapped: Path):
    netlist = parse_verilog_netlist(mapped)
    assert netlist.module_name == "counter"
    assert netlist.port_order == ["clk", "rst_n", "q", "done"]


def test_directions_and_bus_ranges_are_read(mapped: Path):
    netlist = parse_verilog_netlist(mapped)
    assert netlist.inputs["clk"] is None
    assert netlist.outputs["q"] == (3, 0)
    assert netlist.outputs["done"] is None
    assert netlist.wires["n1"] is None
    assert netlist.wires["bus"] == (7, 0)
    # ``one_`` and ``zero_`` are declared here too, but they are constant
    # wires and are removed from the signal set -- see the constants group.


def test_comments_are_removed_before_parsing(mapped: Path):
    """A commented-out instance must not become an instance."""
    netlist = parse_verilog_netlist(mapped)
    assert "spanning" not in " ".join(inst.cell_type for inst in netlist.instances)


def test_a_file_without_a_module_is_refused(tmp_path: Path):
    """An empty netlist would let a caller analyse nothing and report it."""
    path = tmp_path / "empty.v"
    path.write_text("// nothing here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no module declaration"):
        parse_verilog_netlist(path)


# --------------------------------------------------------------------------
# instances
# --------------------------------------------------------------------------

def test_a_primitive_cell_type_is_normalized(mapped: Path):
    netlist = parse_verilog_netlist(mapped)
    cells = {inst.name: inst.cell_type for inst in netlist.instances}
    assert cells["g1"] == "and"
    assert "and" in YOSYS_PRIMITIVE_CELL_TYPES.values()
    assert YOSYS_PRIMITIVE_CELL_TYPES["$_AND_"] == "and"


def test_named_connections_are_lowercased_into_a_mapping(mapped: Path):
    gate = next(i for i in parse_verilog_netlist(mapped).instances
                if i.name == "g1")
    assert gate.named_connections == {"a": "clk", "b": "rst_n", "y": "n1"}
    assert gate.output == "n1"
    assert gate.inputs == ["clk", "rst_n"]


def test_a_positional_buffer_puts_the_output_first(mapped: Path):
    """`buf (y, a)` -- the output is the first connection, not the last."""
    buffer = next(i for i in parse_verilog_netlist(mapped).instances
                  if i.name == "b1")
    assert buffer.cell_type == "buf"
    assert buffer.output == "done"
    assert buffer.inputs == ["n1"]


def test_a_continuous_assignment_becomes_a_buffer_instance(mapped: Path):
    """Every edge then comes from one kind of object."""
    netlist = parse_verilog_netlist(mapped)
    assigns = [i for i in netlist.instances if i.name.startswith("__assign_buf_")]
    assert len(assigns) == 1
    assert assigns[0].cell_type == "buf"
    assert assigns[0].output == "n1"
    assert assigns[0].inputs == ["clk & rst_n"]


def test_a_synthesized_name_collides_with_nothing(tmp_path: Path):
    """The netlist already contains ``__assign_buf_0``; the parser must not
    reuse the name and quietly merge two different things."""
    path = tmp_path / "collide.v"
    path.write_text(
        "module m (a, b);\n"
        "  input a;\n  output b;\n  wire w;\n"
        # An instance actually NAMED __assign_buf_0.  Naming the cell type that
        # way would not collide with anything, so it would prove nothing.
        "  somecell __assign_buf_0 (w, a);\n"
        "  assign w = a;\n"
        "endmodule\n",
        encoding="utf-8",
    )
    netlist = parse_verilog_netlist(path)
    names = [i.name for i in netlist.instances]
    assert len(names) == len(set(names))
    assert "__assign_buf_0" in names and "__assign_buf_0_1" in names


def test_declaration_keywords_are_not_mistaken_for_cells(tmp_path: Path):
    path = tmp_path / "decls.v"
    path.write_text(
        "module m (a, b);\n  input a;\n  output b;\n  wire w;\n"
        "  assign w = a;\n  assign b = w;\nendmodule\n",
        encoding="utf-8",
    )
    netlist = parse_verilog_netlist(path)
    assert all(i.cell_type not in {"input", "output", "wire", "module"}
               for i in netlist.instances)


def test_a_parameter_override_is_stripped(tmp_path: Path):
    """``#(...)`` would otherwise be read as part of the connection list."""
    path = tmp_path / "param.v"
    path.write_text(
        "module m (a, b);\n  input a;\n  output b;\n"
        "  sub #(.WIDTH(4)) u0 (.a(a), .b(b));\n"
        "endmodule\n",
        encoding="utf-8",
    )
    netlist = parse_verilog_netlist(path)
    instance = next(i for i in netlist.instances if i.name == "u0")
    assert instance.cell_type == "sub"
    assert instance.named_connections == {"a": "a", "b": "b"}


def test_a_nested_parenthesis_in_a_connection_does_not_split_the_list(
    tmp_path: Path,
):
    path = tmp_path / "nested.v"
    path.write_text(
        "module m (a);\n  input a;\n  wire w;\n"
        "  sub u0 (.a(a), .b({a, a}), .c(a));\n"
        "endmodule\n",
        encoding="utf-8",
    )
    instance = next(i for i in parse_verilog_netlist(path).instances
                    if i.name == "u0")
    assert set(instance.named_connections) == {"a", "b", "c"}
    assert instance.named_connections["b"] == "{a, a}"


# --------------------------------------------------------------------------
# flip-flops -- the positional conventions
# --------------------------------------------------------------------------

def test_a_flip_flop_is_recognized_from_its_cell_family():
    for cell in ("$_DFF_P_", "DFFR", "SDFF", "\\$_DFFE_NP0_"):
        assert Instance(cell_type=cell, name="x").is_dff is True
    for cell in ("and", "$_AND_", "mux"):
        assert Instance(cell_type=cell, name="x").is_dff is False


def test_a_flip_flops_pins_are_read_from_its_names(tmp_path: Path):
    path = tmp_path / "named_dff.v"
    path.write_text(
        "module m (clk, d, q);\n  input clk;\n  input d;\n  output q;\n"
        "  dff ff0 (.clk(clk), .d(d), .q(q));\nendmodule\n",
        encoding="utf-8",
    )
    ff = next(i for i in parse_verilog_netlist(path).instances
              if i.name == "ff0")
    assert ff.is_dff is True
    assert ff.dff_clock() == "clk"
    assert ff.dff_data() == "d"
    assert ff.dff_q() == "q"


def test_a_positional_flip_flop_uses_the_primitive_pin_order(tmp_path: Path):
    """The order is (clk, rst, d, q), so the output is the fourth connection.

    This is a tool convention, not a language rule, and it is the reason named
    connections are always preferred.
    """
    path = tmp_path / "pos_dff.v"
    path.write_text(
        "module m (clk, rst, d, q);\n  input clk;\n  input rst;\n"
        "  input d;\n  output q;\n"
        "  dff ff0 (clk, rst, d, q);\nendmodule\n",
        encoding="utf-8",
    )
    ff = next(i for i in parse_verilog_netlist(path).instances
              if i.name == "ff0")
    assert ff.output == "q"
    assert ff.inputs == ["clk", "rst", "d"]
    assert ff.dff_clock() == "clk"
    assert ff.dff_data() == "d"


def test_a_positional_flip_flop_with_only_three_connections_has_no_output(
    tmp_path: Path,
):
    """Guessing an output from an incomplete list would invent an edge."""
    path = tmp_path / "short_dff.v"
    path.write_text(
        "module m (clk, d);\n  input clk;\n  input d;\n"
        "  dff ff0 (clk, 1'b0, d);\nendmodule\n",
        encoding="utf-8",
    )
    ff = next(i for i in parse_verilog_netlist(path).instances
              if i.name == "ff0")
    assert ff.output is None
    assert ff.dff_q() is None


def test_a_named_flip_flop_without_a_reset_does_not_gain_a_positional_hole(
    tmp_path: Path,
):
    """Empty pins are dropped, so the data pin does not shift position."""
    path = tmp_path / "no_reset.v"
    path.write_text(
        "module m (clk, d, q);\n  input clk;\n  input d;\n  output q;\n"
        "  dff ff0 (.clk(clk), .d(d), .q(q));\nendmodule\n",
        encoding="utf-8",
    )
    ff = next(i for i in parse_verilog_netlist(path).instances
              if i.name == "ff0")
    assert ff.inputs == ["clk", "d"]
    assert ff.dff_clock() == "clk"
    assert ff.dff_data() == "d"


# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

def test_a_constant_wire_becomes_a_literal(mapped: Path):
    """``one_`` and ``zero_`` are how a synthesis flow ties constants through.

    Treating them as signals would invent edges to a net that carries nothing.
    """
    netlist = parse_verilog_netlist(mapped)
    assert "one_" not in netlist.wires
    assert "zero_" not in netlist.wires
    assert set(CONSTANT_ALIASES) == {"one_", "zero_"}


def test_a_constant_named_wire_that_is_a_port_is_left_alone(tmp_path: Path):
    """The name is only a convention; a port called ``one_`` is a signal."""
    path = tmp_path / "port_named_one.v"
    path.write_text(
        "module m (one_, y);\n  input one_;\n  output y;\n  wire one_;\n"
        "  buf b0 (y, one_);\nendmodule\n",
        encoding="utf-8",
    )
    netlist = parse_verilog_netlist(path)
    assert "one_" in netlist.inputs
    buffer = next(i for i in netlist.instances if i.name == "b0")
    assert buffer.inputs == ["one_"]


def test_a_constant_named_wire_that_is_driven_is_left_alone(tmp_path: Path):
    path = tmp_path / "driven_zero.v"
    path.write_text(
        "module m (a, y);\n  input a;\n  output y;\n  wire zero_;\n"
        "  buf b0 (zero_, a);\n  buf b1 (y, zero_);\nendmodule\n",
        encoding="utf-8",
    )
    netlist = parse_verilog_netlist(path)
    assert "zero_" in netlist.wires
    driven = next(i for i in netlist.instances if i.name == "b0")
    assert driven.output == "zero_"


# --------------------------------------------------------------------------
# the netlist as a whole
# --------------------------------------------------------------------------

def test_all_signals_lists_each_name_once(mapped: Path):
    signals = parse_verilog_netlist(mapped).all_signals()
    assert len(signals) == len(set(signals))
    assert "clk" in signals and "n1" in signals


def test_all_signals_excludes_a_bit_literal(tmp_path: Path):
    """A literal is not a net; including it would create an edge to nothing."""
    path = tmp_path / "literal.v"
    path.write_text(
        "module m (a, y);\n  input a;\n  output y;\n"
        "  and g0 (y, a, 1'b1);\nendmodule\n",
        encoding="utf-8",
    )
    signals = parse_verilog_netlist(path).all_signals()
    assert not any(signal.startswith("1'b") for signal in signals)


def test_a_signal_resolves_to_itself(mapped: Path):
    netlist = parse_verilog_netlist(mapped)
    assert netlist.resolve_signal_or_instance("n1") == "n1"


def test_an_instance_resolves_to_the_net_it_drives(mapped: Path):
    netlist = parse_verilog_netlist(mapped)
    assert netlist.resolve_signal_or_instance("g1") == "n1"
    assert netlist.resolve_signal_or_instance("b1") == "done"


def test_a_flip_flop_resolves_to_its_q(tmp_path: Path):
    """A report may name the register; the graph knows the net it drives."""
    path = tmp_path / "ff.v"
    path.write_text(
        "module m (clk, d, q);\n  input clk;\n  input d;\n  output q;\n"
        "  dff ff0 (.clk(clk), .d(d), .q(q));\nendmodule\n",
        encoding="utf-8",
    )
    netlist = parse_verilog_netlist(path)
    assert netlist.resolve_signal_or_instance("ff0") == "q"


def test_a_report_token_is_stripped_before_resolution(mapped: Path):
    """Timing reports quote and punctuate names."""
    netlist = parse_verilog_netlist(mapped)
    assert netlist.resolve_signal_or_instance('"n1",') == "n1"


def test_an_unknown_token_resolves_to_nothing(mapped: Path):
    assert parse_verilog_netlist(mapped).resolve_signal_or_instance("absent") is None


def test_lookups_are_cached_and_consistent(mapped: Path):
    netlist = parse_verilog_netlist(mapped)
    first = netlist.all_signals()
    assert netlist.all_signals() == first
    assert netlist.has_signal("clk") is True
    assert netlist.has_signal("absent") is False
    assert netlist.get_instance("g1") is netlist.get_instance("g1")
