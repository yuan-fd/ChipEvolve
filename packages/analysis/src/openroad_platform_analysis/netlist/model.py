import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple, Union
PrimitiveType = str
BusRange = Optional[Tuple[int, int]]

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
    inputs: List[str] = field(default_factory=list)
    named_connections: Dict[str, str] = field(default_factory=dict)

    @property
    def is_dff(self):
        cell = re.sub(r"^[\\$_]+", "", self.cell_type).split("_")[0].upper()
        return cell in ('DFF', 'DFFE', 'DFFP', 'DFFR', 'SDFF', 'DFF_NEG')

    @property
    def is_combinational(self):
        return self.cell_type.lower() in {'and', 'or', 'nand', 'nor', 'not', 'buf', 'xor', 'xnor', 'mux'}

    def dff_clock(self):
        if not self.is_dff:
            return None
        for key in ('clk', 'ck', 'clock'):
            if key in self.named_connections:
                return self.named_connections[key]
        return self.inputs[0] if self.inputs else None

    def dff_data(self):
        if not self.is_dff:
            return None
        if 'd' in self.named_connections:
            return self.named_connections["d"]
        return self.inputs[2] if len(self.inputs) >= 3 else None

    def dff_q(self):
        if not self.is_dff:
            return None
        for key in ('q', 'qn'):
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
    port_order: List[str]
    inputs: Dict[str, BusRange]
    outputs: Dict[str, BusRange]
    wires: Dict[str, BusRange]
    instances: List[Instance]
    source_path: Optional[Path] = None
    _all_signals_cache: Optional[List[str]] = field(default=None, init=False, repr=False)
    _all_signal_set_cache: Optional[Set[str]] = field(default=None, init=False, repr=False)
    _instance_by_name_cache: Optional[Dict[str, Instance]] = field(default=None, init=False, repr=False)

    def has_signal(self, signal):
        if self._all_signal_set_cache is None:
            self._all_signal_set_cache = set(self.all_signals())
        return signal in self._all_signal_set_cache

    def all_signals(self):
        if self._all_signals_cache is not None:
            return list(self._all_signals_cache)
        seen = set()
        ordered: List[str] = []
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
                if name not in seen and (not name.startswith("1'b")):
                    seen.add(name)
                    ordered.append(name)
        self._all_signals_cache = ordered
        return ordered

    def resolve_signal_or_instance(self, token):
        token = token.strip().strip('"\'`,.?!')
        if self.has_signal(token):
            return token
        inst = self.get_instance(token)
        if inst is not None:
            if inst.is_dff:
                return inst.dff_q()
            return inst.output
        return None

    def get_instance(self, name):
        if self._instance_by_name_cache is None:
            self._instance_by_name_cache = {inst.name: inst for inst in self.instances}
        return self._instance_by_name_cache.get(name)
