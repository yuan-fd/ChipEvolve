"""A bounded Verilog netlist reader for the EDAIR index.

Ported from the frozen v1 implementation.  It reads the *mapped* netlist a
synthesis tool emits, not arbitrary Verilog: a module header, port and wire
declarations, cell instances, and ``assign`` statements.

What it deliberately does not do:

* It does not elaborate.  A netlist whose semantics depend on parameters,
  generate blocks or arithmetic operators is read structurally, and the caller
  is expected to treat an unexpected shape as a reason to look at the raw
  artifact rather than as a fact.
* It does not fail on a construct it does not recognise.  Unrecognised text
  simply produces no instance, and the raw netlist remains the source of truth.

Two conventions are recorded rather than inferred, because both are fragile:

* **Positional connections follow the tool's pin order, not a language rule.**
  A ``dff`` primitive is ``(clk, rst, d, q)``; a ``buf``/``not`` is
  ``(y, a)``; anything else is ``(y, a, b, s)``.  Named connections are always
  preferred, and the fallback exists only for the pre-mapped primitives that
  have no names.
* **``one_`` and ``zero_`` are constant wires, not signals.**  A synthesis flow
  ties constants through wires with those exact names; treating them as signals
  would invent edges to a net that carries no information.  They are only
  rewritten when they are not ports and nothing drives them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

BusRange = Optional[tuple[int, int]]

#: ``input``/``output``/``wire`` with an optional bus range and a name list.
_DECL_RE = re.compile(
    r"\b(input|output|wire)\b(?:\s+(?:wire|reg|logic|signed))*\s*"
    r"(\[[^\]]+\])?\s*([^;]+);",
    flags=re.IGNORECASE,
)
#: One module, capturing its header port list and its body.
_MODULE_RE = re.compile(
    r"module\s+([A-Za-z_][\w$]*)\s*\((.*?)\)\s*;(?P<body>.*)endmodule",
    flags=re.DOTALL | re.IGNORECASE,
)
#: A cell instance followed by its connection list.
_INSTANCE_RE = re.compile(
    r"^\s*(\\?\$?[A-Za-z_][\w$]*_?)\s+([A-Za-z_\\][\w$\\\[\]]*)\s*"
    r"\((.*?)\)\s*;\s*$",
    flags=re.DOTALL | re.MULTILINE,
)
#: An instance with a parameter override, which is stripped before parsing.
_PARAMETERIZED_INSTANCE_RE = re.compile(
    r"(^\s*\\?\$?[A-Za-z_][\w$]*_?\s*)#\s*"
    r"\((?:[^()]|\([^()]*\))*\)\s*([A-Za-z_\\][\w$\\\[\]]*\s*\()",
    flags=re.DOTALL | re.MULTILINE,
)
_ASSIGN_RE = re.compile(
    r"^\s*assign\s+([A-Za-z_\\][\w$\\\[\]]*)\s*=\s*([^;]+?)\s*;\s*$",
    flags=re.MULTILINE,
)

#: Pre-mapped primitive names, from both the internal cell forms and the
#: operator forms a synthesis tool may leave behind.  Recorded exactly: an
#: unrecognised name stays as it is, and the caller sees the raw cell type.
YOSYS_PRIMITIVE_CELL_TYPES: dict[str, str] = {
    "$_AND_": "and", "$_OR_": "or", "$_NAND_": "nand", "$_NOR_": "nor",
    "$_NOT_": "not", "$_BUF_": "buf", "$_XOR_": "xor", "$_XNOR_": "xnor",
    "$_MUX_": "mux", "$_ANDNOT_": "andnot", "$_ORNOT_": "ornot",
    "$and": "and", "$or": "or", "$nand": "nand", "$nor": "nor",
    "$not": "not", "$logic_not": "not", "$buf": "buf", "$xor": "xor",
    "$xnor": "xnor", "$mux": "mux",
}

#: Names a synthesis flow uses for constant wires.
CONSTANT_ALIASES = {"one_": "1'b1", "zero_": "1'b0"}

#: Cell types that a declaration regex can match and that are never instances.
NOT_A_CELL = frozenset({"input", "output", "wire", "module"})


@dataclass
class PortDecl:
    name: str
    direction: str
    width: BusRange = None


@dataclass
class Instance:
    cell_type: str
    name: str
    output: Optional[str] = None
    inputs: list[str] = field(default_factory=list)
    named_connections: dict[str, str] = field(default_factory=dict)

    @property
    def is_dff(self) -> bool:
        """Whether this is a flip-flop, judged from the leading cell family.

        A leading ``\\`` or ``$`` is dropped and the first underscore-separated
        segment is taken, so ``$_SDFF_PP0_`` and ``DFFR`` both register.
        """
        cell = re.sub(r"^[\\$_]+", "", self.cell_type).split("_")[0].upper()
        return cell in {"DFF", "DFFE", "DFFP", "DFFR", "SDFF", "DFF_NEG"}

    @property
    def is_combinational(self) -> bool:
        return self.cell_type.lower() in {
            "and", "or", "nand", "nor", "not", "buf", "xor", "xnor", "mux",
        }

    def dff_clock(self) -> Optional[str]:
        if not self.is_dff:
            return None
        for key in ("clk", "ck", "clock"):
            if key in self.named_connections:
                return self.named_connections[key]
        if self.named_connections:
            # The pins were named and none of them was a clock, so there is no
            # clock to report.  Falling through to the positional slot here
            # would return the DATA net, because a named dff with no clock has
            # its data first in the filtered input list -- and a register
            # reported as clocked by its own data is worse than one reported as
            # unclocked.
            return None
        # Positional pins, so slot 0 really is the clock.
        return self.inputs[0] if self.inputs else None

    def dff_data(self) -> Optional[str]:
        if not self.is_dff:
            return None
        if "d" in self.named_connections:
            return self.named_connections["d"]
        if self.named_connections:
            return None
        # Pin 2, because the positional primitive order is (clk, rst, d, q).
        return self.inputs[2] if len(self.inputs) >= 3 else None

    def dff_q(self) -> Optional[str]:
        if not self.is_dff:
            return None
        for key in ("q", "qn"):
            if key in self.named_connections:
                return self.named_connections[key]
        return self.output


@dataclass
class Edge:
    src: str
    dst: str
    instance: Instance


@dataclass
class Netlist:
    module_name: str
    port_order: list[str]
    inputs: dict[str, BusRange]
    outputs: dict[str, BusRange]
    wires: dict[str, BusRange]
    instances: list[Instance]
    source_path: Optional[Path] = None
    _all_signals_cache: Optional[list[str]] = field(
        default=None, init=False, repr=False)
    _all_signal_set_cache: Optional[set[str]] = field(
        default=None, init=False, repr=False)
    _instance_by_name_cache: Optional[dict[str, Instance]] = field(
        default=None, init=False, repr=False)

    def has_signal(self, signal: str) -> bool:
        if self._all_signal_set_cache is None:
            self._all_signal_set_cache = set(self.all_signals())
        return signal in self._all_signal_set_cache

    def all_signals(self) -> list[str]:
        """Every signal name, in a stable order, with constants excluded.

        Constants are excluded because a literal is not a net: including
        ``1'b1`` would create an edge to something that carries no information.
        """
        if self._all_signals_cache is not None:
            return list(self._all_signals_cache)
        seen: set[str] = set()
        ordered: list[str] = []
        for bucket in (self.inputs, self.outputs, self.wires):
            for name in bucket:
                if name not in seen:
                    seen.add(name)
                    ordered.append(name)
        for inst in self.instances:
            if inst.output and inst.output not in seen:
                seen.add(inst.output)
                ordered.append(inst.output)
            for name in inst.inputs:
                if name not in seen and not name.startswith("1'b"):
                    seen.add(name)
                    ordered.append(name)
        self._all_signals_cache = ordered
        return ordered

    def resolve_signal_or_instance(self, token: str) -> Optional[str]:
        """Resolve a report token to a signal name.

        A timing report names either a net or an instance.  An instance is
        resolved to the net it drives, and a flip-flop to its Q, so that a path
        endpoint in the report lands on the same name the graph uses.
        """
        token = token.strip().strip("\"'`,.?!")
        if self.has_signal(token):
            return token
        inst = self.get_instance(token)
        if inst is not None:
            if inst.is_dff:
                return inst.dff_q()
            return inst.output
        return None

    def get_instance(self, name: str) -> Optional[Instance]:
        if self._instance_by_name_cache is None:
            self._instance_by_name_cache = {i.name: i for i in self.instances}
        return self._instance_by_name_cache.get(name)


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def _parse_bus(raw: str | None) -> BusRange:
    """A bus range needs exactly two numbers; anything else is not a range."""
    if not raw:
        return None
    numbers = re.findall(r"-?\d+", raw)
    if len(numbers) != 2:
        return None
    return (int(numbers[0]), int(numbers[1]))


def _split_names(raw: str) -> list[str]:
    cleaned = []
    for part in raw.split(","):
        token = re.sub(r"\b(wire|reg|logic|signed)\b", "", part,
                       flags=re.IGNORECASE).strip()
        if token:
            cleaned.append(token)
    return cleaned


def _split_connections(raw: str) -> list[str]:
    """Split a connection list on top-level commas.

    Parenthesis depth is tracked so a nested call inside a connection does not
    split the list at its own comma.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in raw:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_named_connections(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for token in _split_connections(raw):
        match = re.match(r"\.(\w+)\s*\(\s*(.*?)\s*\)\s*$", token,
                         flags=re.DOTALL)
        if match:
            mapping[match.group(1).lower()] = match.group(2).strip()
    return mapping


def _strip_instance_parameters(body: str) -> str:
    return _PARAMETERIZED_INSTANCE_RE.sub(r"\1\2", body)


def _normalize_cell_type(cell_type: str) -> str:
    token = cell_type.strip()
    if token.startswith("\\"):
        token = token[1:]
    return YOSYS_PRIMITIVE_CELL_TYPES.get(token, token)


def _normalize_identifier(name: str) -> str:
    token = name.strip()
    return token[1:] if token.startswith("\\") else token


def _constant_aliases(inputs: dict, outputs: dict, wires: dict,
                      instances: list[Instance]) -> dict[str, str]:
    """Which constant wires really are constants.

    Only a wire that is not a port and is driven by nothing is treated as a
    constant.  A name that a design actually uses as a port or drives from logic
    is a signal, whatever it is called.
    """
    driven = {inst.output for inst in instances if inst.output}
    ports = set(inputs) | set(outputs)
    return {
        name: literal for name, literal in CONSTANT_ALIASES.items()
        if name in wires and name not in ports and name not in driven
    }


def _rewrite_instance_signal_refs(instances: list[Instance],
                                  aliases: dict[str, str]) -> None:
    if not aliases:
        return
    for inst in instances:
        inst.inputs = [aliases.get(signal, signal) for signal in inst.inputs]
        if inst.named_connections:
            inst.named_connections = {
                pin: aliases.get(signal, signal)
                for pin, signal in inst.named_connections.items()
            }


def _positional_to_instance(cell_type: str, name: str,
                            conns: list[str]) -> Instance:
    cell_type = _normalize_cell_type(cell_type)
    name = _normalize_identifier(name)
    lower = cell_type.lower()
    if lower in {"buf", "not"}:
        output = conns[0] if conns else None
        inputs = conns[1:2]
    elif lower == "dff":
        # Primitive order is (clk, rst, d, q): the output is fourth.
        output = conns[3] if len(conns) >= 4 else None
        inputs = conns[:3]
    else:
        output = conns[0] if conns else None
        inputs = conns[1:]
    return Instance(cell_type=cell_type, name=name, output=output, inputs=inputs)


def _named_to_instance(cell_type: str, name: str,
                       mapping: dict[str, str]) -> Instance:
    cell_type = _normalize_cell_type(cell_type)
    name = _normalize_identifier(name)
    lower = cell_type.lower()
    if lower == "dff":
        output = mapping.get("q") or mapping.get("qn")
        ordered = [
            mapping.get("clk") or mapping.get("ck") or mapping.get("clock") or "",
            (mapping.get("rst_n") or mapping.get("rn")
             or mapping.get("reset_n") or mapping.get("sn") or ""),
            mapping.get("d", ""),
        ]
        # Empty pins are dropped, so a dff with no reset does not gain a
        # positional hole that would shift everything after it.
        inputs = [item for item in ordered if item]
    elif lower in {"buf", "not"}:
        output = mapping.get("y") or mapping.get("o") or mapping.get("out")
        inputs = [mapping[key] for key in ("a", "in", "i") if key in mapping]
    else:
        output = mapping.get("y") or mapping.get("o") or mapping.get("out")
        inputs = [mapping[key] for key in ("a", "b", "s") if key in mapping]
    return Instance(cell_type=cell_type, name=name, output=output,
                    inputs=inputs, named_connections=mapping)


def parse_verilog_netlist(path: str | Path) -> Netlist:
    """Read one mapped netlist file.

    A file with no module declaration is an error: returning an empty netlist
    would let a caller compute a path analysis over nothing and report it as a
    result.
    """
    resolved = Path(path).resolve()
    text = _strip_comments(resolved.read_text(encoding="utf-8", errors="ignore"))
    match = _MODULE_RE.search(text)
    if not match:
        raise ValueError(f"no module declaration found in {resolved}")

    module_name = match.group(1)
    header_ports = [token.strip() for token in match.group(2).split(",")
                    if token.strip()]
    body = match.group("body")
    instance_body = _strip_instance_parameters(body)

    inputs: dict[str, BusRange] = {}
    outputs: dict[str, BusRange] = {}
    wires: dict[str, BusRange] = {}
    for kind, width_raw, names_raw in _DECL_RE.findall(body):
        width = _parse_bus(width_raw)
        for name in _split_names(names_raw):
            if kind.lower() == "input":
                inputs[name] = width
            elif kind.lower() == "output":
                outputs[name] = width
            else:
                wires[name] = width

    instances: list[Instance] = []
    for cell_type, inst_name, conn_blob in _INSTANCE_RE.findall(instance_body):
        if cell_type.lower() in NOT_A_CELL:
            continue
        if "." in conn_blob:
            instances.append(_named_to_instance(
                cell_type, inst_name, _parse_named_connections(conn_blob)))
        else:
            conns = [token.strip() for token in _split_connections(conn_blob)]
            instances.append(_positional_to_instance(cell_type, inst_name, conns))

    used_names = {inst.name for inst in instances}
    for index, (lhs, rhs) in enumerate(_ASSIGN_RE.findall(body)):
        # A continuous assignment becomes a buffer instance so that every edge
        # in the graph comes from one kind of object.  The name is made unique
        # rather than assuming the netlist has no ``__assign_buf_0``.
        base = f"__assign_buf_{index}"
        assign_name = base
        suffix = 0
        while assign_name in used_names:
            suffix += 1
            assign_name = f"{base}_{suffix}"
        used_names.add(assign_name)
        instances.append(Instance(
            cell_type="buf", name=assign_name,
            output=_normalize_identifier(lhs), inputs=[rhs.strip()],
        ))

    aliases = _constant_aliases(inputs, outputs, wires, instances)
    _rewrite_instance_signal_refs(instances, aliases)
    for alias in aliases:
        wires.pop(alias, None)

    return Netlist(
        module_name=module_name, port_order=header_ports, inputs=inputs,
        outputs=outputs, wires=wires, instances=instances, source_path=resolved,
    )
