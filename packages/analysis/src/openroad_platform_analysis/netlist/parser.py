import re
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple, Union
from .model import Instance, Netlist, PortDecl
DECL_RE = re.compile('\\b(input|output|wire)\\b(?:\\s+(?:wire|reg|logic|signed))*\\s*(\\[[^\\]]+\\])?\\s*([^;]+);', flags=re.IGNORECASE)
MODULE_RE = re.compile('module\\s+([A-Za-z_][\\w$]*)\\s*\\((.*?)\\)\\s*;(?P<body>.*)endmodule', flags=re.DOTALL | re.IGNORECASE)
INSTANCE_RE = re.compile('^\\s*(\\\\?\\$?[A-Za-z_][\\w$]*_?)\\s+([A-Za-z_\\\\][\\w$\\\\\\[\\]]*)\\s*\\((.*?)\\)\\s*;\\s*$', flags=re.DOTALL | re.MULTILINE)
PARAMETERIZED_INSTANCE_RE = re.compile('(^\\s*\\\\?\\$?[A-Za-z_][\\w$]*_?\\s*)#\\s*\\((?:[^()]|\\([^()]*\\))*\\)\\s*([A-Za-z_\\\\][\\w$\\\\\\[\\]]*\\s*\\()', flags=re.DOTALL | re.MULTILINE)
ASSIGN_RE = re.compile('^\\s*assign\\s+([A-Za-z_\\\\][\\w$\\\\\\[\\]]*)\\s*=\\s*([^;]+?)\\s*;\\s*$', flags=re.MULTILINE)
YOSYS_PRIMITIVE_CELL_TYPES = {'$_AND_': 'and', '$_OR_': 'or', '$_NAND_': 'nand', '$_NOR_': 'nor', '$_NOT_': 'not', '$_BUF_': 'buf', '$_XOR_': 'xor', '$_XNOR_': 'xnor', '$_MUX_': 'mux', '$_ANDNOT_': 'andnot', '$_ORNOT_': 'ornot', '$and': 'and', '$or': 'or', '$nand': 'nand', '$nor': 'nor', '$not': 'not', '$logic_not': 'not', '$buf': 'buf', '$xor': 'xor', '$xnor': 'xnor', '$mux': 'mux'}

def _strip_comments(text):
    text = re.sub('/\\*.*?\\*/', '', text, flags=re.DOTALL)
    text = re.sub('//.*', '', text)
    return text

def _parse_bus(raw):
    if not raw:
        return None
    numbers = re.findall('-?\\d+', raw)
    if len(numbers) != 2:
        return None
    return (int(numbers[0]), int(numbers[1]))

def _split_names(raw):
    cleaned = []
    for part in raw.split(','):
        token = re.sub('\\b(wire|reg|logic|signed)\\b', '', part, flags=re.IGNORECASE).strip()
        if token:
            cleaned.append(token)
    return cleaned

def _split_connections(raw):
    parts = []
    current = []
    depth = 0
    for ch in raw:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        if ch == ',' and depth == 0:
            part = ''.join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(ch)
    tail = ''.join(current).strip()
    if tail:
        parts.append(tail)
    return parts

def _parse_named_connections(raw):
    mapping = {}
    for token in _split_connections(raw):
        match = re.match('\\.(\\w+)\\s*\\(\\s*(.*?)\\s*\\)\\s*$', token, flags=re.DOTALL)
        if match:
            mapping[match.group(1).lower()] = match.group(2).strip()
    return mapping

def _strip_instance_parameters(body):
    return PARAMETERIZED_INSTANCE_RE.sub('\\1\\2', body)

def _normalize_cell_type(cell_type):
    token = cell_type.strip()
    if token.startswith('\\'):
        token = token[1:]
    return YOSYS_PRIMITIVE_CELL_TYPES.get(token, token)

def _normalize_identifier(name):
    token = name.strip()
    if token.startswith('\\'):
        token = token[1:]
    return token

def _constant_aliases(inputs, outputs, wires, instances):
    aliases = {'one_': "1'b1", 'zero_': "1'b0"}
    driven = {inst.output for inst in instances if inst.output}
    ports = set(inputs) | set(outputs)
    return {name: literal for name, literal in aliases.items() if name in wires and name not in ports and (name not in driven)}

def _rewrite_instance_signal_refs(instances, aliases):
    if not aliases:
        return
    for inst in instances:
        inst.inputs = [aliases.get(signal, signal) for signal in inst.inputs]
        if inst.named_connections:
            inst.named_connections = {pin: aliases.get(signal, signal) for pin, signal in inst.named_connections.items()}

def _positional_to_instance(cell_type, name, conns):
    cell_type = _normalize_cell_type(cell_type)
    name = _normalize_identifier(name)
    lower = cell_type.lower()
    if lower in {'buf', 'not'}:
        output = conns[0] if conns else None
        inputs = conns[1:2]
    elif lower == 'dff':
        output = conns[3] if len(conns) >= 4 else None
        inputs = conns[:3]
    else:
        output = conns[0] if conns else None
        inputs = conns[1:]
    return Instance(cell_type=cell_type, name=name, output=output, inputs=inputs)

def _named_to_instance(cell_type, name, mapping):
    cell_type = _normalize_cell_type(cell_type)
    name = _normalize_identifier(name)
    lower = cell_type.lower()
    if lower == 'dff':
        output = mapping.get("q") or mapping.get("qn")
        ordered_inputs = [mapping.get("clk") or mapping.get("ck") or mapping.get("clock") or '', mapping.get("rst_n") or mapping.get("rn") or mapping.get("reset_n") or mapping.get("sn") or '', mapping.get("d", '')]
        inputs = [item for item in ordered_inputs if item]
    elif lower in {'buf', 'not'}:
        output = mapping.get("y") or mapping.get("o") or mapping.get("out")
        inputs = [mapping[key] for key in ('a', 'in', 'i') if key in mapping]
    else:
        output = mapping.get("y") or mapping.get("o") or mapping.get("out")
        inputs = [mapping[key] for key in ('a', 'b', 's') if key in mapping]
    return Instance(cell_type=cell_type, name=name, output=output, inputs=inputs, named_connections=mapping)

def parse_verilog_netlist(path):
    resolved = Path(path).resolve()
    text = _strip_comments(resolved.read_text(encoding='utf-8', errors='ignore'))
    match = MODULE_RE.search(text)
    if not match:
        raise ValueError(f'Unable to parse top module from {resolved}')
    module_name = match.group(1)
    header_ports = [token.strip() for token in match.group(2).split(',') if token.strip()]
    body = match.group('body')
    instance_body = _strip_instance_parameters(body)
    inputs = {}
    outputs = {}
    wires = {}
    for kind, width_raw, names_raw in DECL_RE.findall(body):
        width = _parse_bus(width_raw)
        for name in _split_names(names_raw):
            base_name = name.strip()
            if kind.lower() == 'input':
                inputs[base_name] = width
            elif kind.lower() == 'output':
                outputs[base_name] = width
            else:
                wires[base_name] = width
    instances = []
    for cell_type, inst_name, conn_blob in INSTANCE_RE.findall(instance_body):
        if cell_type.lower() in {'input', 'output', 'wire', 'module'}:
            continue
        if '.' in conn_blob:
            mapping = _parse_named_connections(conn_blob)
            instances.append(_named_to_instance(cell_type, inst_name, mapping))
        else:
            conns = [token.strip() for token in _split_connections(conn_blob)]
            instances.append(_positional_to_instance(cell_type, inst_name, conns))
    used_instance_names = {inst.name for inst in instances}
    for idx, (lhs, rhs) in enumerate(ASSIGN_RE.findall(body)):
        lhs = _normalize_identifier(lhs)
        rhs = rhs.strip()
        base_name = f'__assign_buf_{idx}'
        assign_name = base_name
        suffix = 0
        while assign_name in used_instance_names:
            suffix += 1
            assign_name = f'{base_name}_{suffix}'
        used_instance_names.add(assign_name)
        instances.append(Instance(cell_type='buf', name=assign_name, output=lhs, inputs=[rhs]))
    constant_aliases = _constant_aliases(inputs, outputs, wires, instances)
    _rewrite_instance_signal_refs(instances, constant_aliases)
    for alias in constant_aliases:
        wires.pop(alias, None)
    return Netlist(module_name=module_name, port_order=header_ports, inputs=inputs, outputs=outputs, wires=wires, instances=instances, source_path=resolved)
