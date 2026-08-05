#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电路可视化：Graphviz 真实连线原理图 + 依赖无关的概览回退。

读门级网表 Verilog，生成 SVG 框图，每个门类型画具体逻辑符号 + 数量。
`generate_schematic_svg` 使用成熟 Graphviz 布局；`generate_svg` 只作为
Graphviz 不可用时的标准库回退。

CLI:
    python3 tools/circuit_overview.py demo_output/gen_xxx/counter_gates.v
    python3 tools/circuit_overview.py demo_output/gen_xxx/counter_gates.v -o diagram.svg
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path


# ── 门符号 SVG 路径（24×20 网格内绘制） ──────────────────────────
# 每个符号：返回 (svg_path, 色标)
GATE_SYMBOLS = {
    "AND": (
        'M 2,2 L 2,18 L 12,18 A 10,10 0 0,0 12,2 Z',      # D 形
        "#0969da"
    ),
    "NAND": (
        'M 2,2 L 2,18 L 12,18 A 10,10 0 0,0 12,2 Z M 18,10 A 1.5,1.5 0 1,0 18,9.9',
        "#0969da"
    ),
    "OR": (
        'M 2,18 Q 6,10 2,2 Q 10,6 16,10 Q 10,14 2,18 Z',  # 子弹形
        "#1a7f37"
    ),
    "NOR": (
        'M 2,18 Q 6,10 2,2 Q 10,6 16,10 Q 10,14 2,18 Z M 20,10 A 1.5,1.5 0 1,0 20,9.9',
        "#1a7f37"
    ),
    "XOR": (
        'M 2,18 Q 6,10 2,2 Q 10,6 16,10 Q 10,14 2,18 Z M 22,10 L 18,6 M 22,10 L 18,14',
        "#bf8700"
    ),
    "XNOR": (
        'M 2,18 Q 6,10 2,2 Q 10,6 16,10 Q 10,14 2,18 Z M 20,10 A 1.5,1.5 0 1,0 20,9.9 M 22,10 L 18,6 M 22,10 L 18,14',
        "#bf8700"
    ),
    "NOT": (
        'M 2,2 L 2,18 L 14,10 Z M 18,10 A 1.5,1.5 0 1,0 18,9.9',
        "#cf222e"
    ),
    "BUF": (
        'M 2,2 L 2,18 L 14,10 Z',
        "#656d76"
    ),
}

# 门色映射（兜底）
FALLBACK_COLOR = "#8250df"


def _gate_symbol(cell_type: str, x: float, y: float, size: float = 24) -> str:
    """在指定位置画一个门符号，返回 SVG 元素字符串。"""
    t = cell_type.upper()
    for prefix, (path_d, color) in GATE_SYMBOLS.items():
        if t.startswith(prefix):
            break
    else:
        path_d, color = None, FALLBACK_COLOR
        return f'<text x="{x + size * 0.3}" y="{y + size * 0.7}" font-size="{size * 0.45}" fill="{color}" font-weight="600">{cell_type[:4]}</text>'

    if path_d is None:
        return ""

    scale = size / 24.0
    return (
        f'<g transform="translate({x},{y}) scale({scale})">'
        f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>'
        f'</g>'
    )


def parse_ports_and_gates(text: str) -> dict:
    """从门级网表中提取端口、门类型和连接。"""
    text = re.sub(r"//[^\n]*|/\*.*?\*/", " ", text, flags=re.S)

    module_m = re.search(r"\bmodule\s+(\w+)\s*(?:#\([^)]*\))?\s*\(([^)]*)\)", text)
    if not module_m:
        return {"error": "找不到 module 声明"}
    name = module_m.group(1)

    # 区分 input/output
    inputs, outputs = [], []
    for m in re.finditer(
        r"\b(input|output|inout)\s+(?:\w+\s+)*(?:\[[^\]]*\]\s+)?(\w+)", text
    ):
        direction = m.group(1)
        pname = m.group(2)
        if direction == "input":
            inputs.append(pname)
        elif direction == "output":
            outputs.append(pname)

    # 提取 instance 和 cell type（Yosys 用 $_AND_ 等含 $ 的名字）
    instances = []
    instances = []
    for m in re.finditer(r"^\s*(\S+)\s+(\w+)\s*\(", text, re.M):
        cell_type, inst_name = m.group(1), m.group(2)
        short = re.sub(r"^[\\$_\s]+", "", cell_type).upper().split("_")[0] or cell_type
        if short in ("MODULE", "ENDMODULE", "INPUT", "OUTPUT", "INOUT",
                      "WIRE", "REG", "ASSIGN", "ALWAYS", "BEGIN", "END"):
            continue
        instances.append({"cell": cell_type, "name": inst_name, "short": short})
    counts = Counter(i["short"] for i in instances)
    dff_types = {"DFF", "DFFE", "DFFP", "DFFR", "SDFF", "LATCH"}
    dff_count = sum(
        c for k, c in counts.items() if any(d in k.upper() for d in dff_types)
    )
    gate_total = sum(
        c for k, c in counts.items()
        if not any(d in k.upper() for d in dff_types)
    )

    return {
        "name": name,
        "inputs": inputs,
        "outputs": outputs,
        "instances": len(instances),
        "dff_count": dff_count,
        "gate_count": gate_total,
        "gates": dict(counts.most_common()),
    }


def generate_svg(data: dict) -> str:
    """生成 SVG 框图，画具体门符号。"""
    if data.get("error"):
        return f"<svg><text>{data['error']}</text></svg>"

    name = data.get("name", "unknown")
    inputs = data.get("inputs", [])
    outputs = data.get("outputs", [])
    gates = data.get("gates", {})
    dff_count = data.get("dff_count", 0)
    gate_count = data.get("gate_count", 0)

    # 只显示非 DFF 的门
    gate_items = [(k, v) for k, v in gates.items()
                   if not any(d in k.upper() for d in ("DFF", "LATCH", "FF"))]
    dff_items = [(k, v) for k, v in gates.items()
                  if any(d in k.upper() for d in ("DFF", "LATCH", "FF"))]

    in_ports = inputs[:12]
    out_ports = outputs[:12]
    in_more = len(inputs) - 12
    out_more = len(outputs) - 12

    # 布局
    W = 800
    top_h = 60           # 标题区高度
    port_col_w = 110     # 端口列宽度
    margin = 36
    body_y = top_h + 16
    body_x = margin + port_col_w + 20
    body_w = W - 2 * body_x
    body_h = max(140, 30 + max(len(gate_items), 1) * 60 + 20)
    body_h = min(body_h, 500)
    H = body_y + body_h + margin + 20

    # 门卡片布局
    cards_per_row = max(2, body_w // 130)
    card_w = min(120, body_w // cards_per_row)
    card_h = 50
    cols = max(2, body_w // (card_w + 8))
    rows = max(1, (len(gate_items) + cols - 1) // cols)

    # 组合逻辑区域尺寸
    gate_area_x = body_x + 10
    gate_area_y = body_y + 12
    gate_area_w = body_w - 20
    gate_area_h = max(60, rows * (card_h + 8) + 24)

    # 寄存器区域
    reg_x = body_x + body_w - 130
    reg_y = body_y + 12
    reg_w = 120
    reg_h = 60

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
        f'  <rect width="{W}" height="{H}" fill="#f8f9fa" rx="8"/>',
        # 标题
        f'  <text x="{W/2}" y="24" text-anchor="middle" font-family="sans-serif" font-size="15" font-weight="bold" fill="#1f2328">{name}</text>',
        f'  <text x="{W/2}" y="44" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#656d76">Inputs: {len(inputs)}  |  Outputs: {len(outputs)}  |  Gates: {gate_count}  |  Registers: {dff_count}</text>',

        # 主体框（虚线边框）
        f'  <rect x="{body_x}" y="{body_y}" width="{body_w}" height="{body_h}" rx="8" fill="none" stroke="#d0d7de" stroke-width="1" stroke-dasharray="6,4"/>',

        # ── 组合逻辑区域 ──
        f'  <rect x="{gate_area_x}" y="{gate_area_y}" width="{gate_area_w}" height="{gate_area_h}" rx="6" fill="#f0f6ff" stroke="#0969da" stroke-width="1" stroke-dasharray="4,3"/>',
        f'  <text x="{gate_area_x + 8}" y="{gate_area_y + 14}" font-family="sans-serif" font-size="11" font-weight="600" fill="#0969da">Combinational Logic ({gate_count} gates)</text>',

        # 输入 → 组合逻辑 箭头
        f'  <line x1="{margin + port_col_w}" y1="{body_y + body_h/2}" x2="{body_x}" y2="{body_y + body_h/2}" stroke="#656d76" stroke-width="1.2" marker-end="url(#a)"/>',
        # 组合逻辑 → 寄存器 箭头
        f'  <line x1="{gate_area_x + gate_area_w}" y1="{body_y + body_h/2}" x2="{reg_x}" y2="{body_y + body_h/2}" stroke="#656d76" stroke-width="1" marker-end="url(#a_s)"/>',
        # 寄存器 → 组合逻辑 反馈箭头
        f'  <line x1="{reg_x + reg_w}" y1="{body_y + body_h/2}" x2="{gate_area_x + gate_area_w}" y2="{body_y + body_h*0.7}" stroke="#656d76" stroke-width="0.8" stroke-dasharray="4,3" marker-end="url(#a_s)"/>',
        # 组合逻辑 → 输出 箭头
        f'  <line x1="{reg_x + reg_w}" y1="{body_y + body_h/2}" x2="{W - margin}" y2="{body_y + body_h/2}" stroke="#656d76" stroke-width="1.2" marker-end="url(#a)"/>',
    ]

    # ── 画门卡片 ──
    for idx, (gtype, gcount) in enumerate(gate_items):
        col = idx % cols
        row = idx // cols
        cx = gate_area_x + 12 + col * (card_w + 8)
        cy = gate_area_y + 28 + row * (card_h + 8)
        sym_size = 28
        sym_x = cx + 4
        sym_y = cy + (card_h - sym_size) / 2
        label_x = cx + sym_size + 12
        label_y = cy + card_h / 2 - 4
        count_y = cy + card_h / 2 + 12

        lines += [
            f'  <rect x="{cx}" y="{cy}" width="{card_w}" height="{card_h}" rx="5" fill="#fff" stroke="#d0d7de" stroke-width="0.8"/>',
            _gate_symbol(gtype, sym_x, sym_y, sym_size),
            f'  <text x="{label_x}" y="{label_y}" font-family="sans-serif" font-size="13" font-weight="600" fill="#1f2328">{gtype}</text>',
            f'  <text x="{label_x}" y="{count_y}" font-family="sans-serif" font-size="18" font-weight="700" fill="#0969da">×{gcount}</text>',
        ]

    # ── 寄存器区域 ──
    if dff_count > 0:
        lines += [
            f'  <rect x="{reg_x}" y="{reg_y}" width="{reg_w}" height="{reg_h}" rx="6" fill="#fff8f0" stroke="#bf8700" stroke-width="1.5"/>',
            f'  <text x="{reg_x + reg_w/2}" y="{reg_y + 22}" text-anchor="middle" font-family="sans-serif" font-size="12" font-weight="600" fill="#bf8700">Register</text>',
            f'  <text x="{reg_x + reg_w/2}" y="{reg_y + 42}" text-anchor="middle" font-family="sans-serif" font-size="18" font-weight="700" fill="#bf8700">{dff_count}</text>',
        ]
    else:
        lines += [
            f'  <rect x="{reg_x}" y="{reg_y}" width="{reg_w}" height="40" rx="6" fill="#f6f8fa" stroke="#d0d7de" stroke-width="0.8"/>',
            f'  <text x="{reg_x + reg_w/2}" y="{reg_y + 26}" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#656d76">无寄存器</text>',
        ]

    # ── 输入端口 ──
    lines.append(f'  <text x="{margin}" y="{body_y - 4}" font-family="sans-serif" font-size="11" font-weight="600" fill="#1f2328">Inputs</text>')
    for i, p in enumerate(in_ports):
        y = body_y + 10 + i * 22
        lines += [
            f'  <rect x="{margin}" y="{y - 7}" width="{port_col_w}" height="18" rx="4" fill="#ddf4ff" stroke="#0969da" stroke-width="0.8"/>',
            f'  <text x="{margin + 8}" y="{y + 4}" font-family="monospace" font-size="11" fill="#0969da">{p}</text>',
            f'  <line x1="{margin + port_col_w}" y1="{y}" x2="{body_x}" y2="{y}" stroke="#656d76" stroke-width="0.5" stroke-dasharray="2,3"/>',
        ]
    if in_more > 0:
        lines.append(f'  <text x="{margin + 10}" y="{body_y + 10 + len(in_ports) * 22}" font-family="sans-serif" font-size="10" fill="#656d76">+{in_more} more</text>')

    # ── 输出端口 ──
    out_x = reg_x + reg_w + 20
    if out_x < body_x + body_w + 30:
        out_x = body_x + body_w + 30
    out_col_w = min(port_col_w, W - out_x - margin)

    lines.append(f'  <text x="{out_x}" y="{body_y - 4}" font-family="sans-serif" font-size="11" font-weight="600" fill="#1f2328">Outputs</text>')
    for i, p in enumerate(out_ports):
        y = body_y + 10 + i * 22
        lines += [
            f'  <rect x="{out_x}" y="{y - 7}" width="{out_col_w}" height="18" rx="4" fill="#fff8f0" stroke="#bf8700" stroke-width="0.8"/>',
            f'  <text x="{out_x + 8}" y="{y + 4}" font-family="monospace" font-size="11" fill="#bf8700">{p}</text>',
            f'  <line x1="{body_x + body_w}" y1="{y}" x2="{out_x}" y2="{y}" stroke="#656d76" stroke-width="0.5" stroke-dasharray="2,3"/>',
        ]
    if out_more > 0:
        lines.append(f'  <text x="{out_x + 10}" y="{body_y + 10 + len(out_ports) * 22}" font-family="sans-serif" font-size="10" fill="#656d76">+{out_more} more</text>')

    # 箭头定义
    lines += [
        '<defs>',
        '  <marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">',
        '    <path d="M0,0 L10,5 L0,10 Z" fill="#656d76"/>',
        '  </marker>',
        '  <marker id="a_s" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="4" markerHeight="4" orient="auto">',
        '    <path d="M0,0 L8,4 L0,8 Z" fill="#656d76"/>',
        '  </marker>',
        '</defs>',
        '</svg>',
    ]

    return "\n".join(lines)


def generate_schematic_svg(text: str, highlight_path: list | None = None) -> str:
    """
    用 Graphviz 生成带连线电路原理图 SVG。
    highlight_path: 需要高亮的实例名列表（最长路径上的门）。
    """
    from graphviz import Digraph

    text_clean = re.sub(r"//[^\n]*|/\*.*?\*/", " ", text, flags=re.S)
    module_m = re.search(r"\bmodule\s+(\w+)", text_clean)
    if not module_m:
        return '<svg><text>找不到 module 声明</text></svg>'
    module_name = module_m.group(1)

    # 解析端口
    inputs, outputs = {}, {}
    for m in re.finditer(r"\b(input|output)\s+(?:\w+\s+)*(?:\[[^\]]*\]\s+)?(\w+)", text_clean):
        d, p = m.group(1), m.group(2)
        if d == "input": inputs[p] = True
        else: outputs[p] = True
    # 辅助：剥离总线索引 "count[3]" → "count"
    _base = lambda s: re.sub(r"\[\d+\]", "", s) if s else ""

    # 解析实例 + 连接
    instances = []
    for m in re.finditer(
        r"^\s*(\\?[\w$_.]+)\s+(\\?[\w\[\]\\_.]+)\s*\((.*?)\)\s*;\s*$",
        text_clean, re.M | re.S
    ):
        cell_type, inst_name, conn_blob = m.group(1).strip(), m.group(2).strip(), m.group(3)
        short = re.sub(r"^[\\$_\s]+", "", cell_type).upper().split("_")[0] or cell_type
        if short in ("MODULE", "ENDMODULE", "INPUT", "OUTPUT", "WIRE", "REG",
                      "ASSIGN", "ALWAYS", "BEGIN", "END", "INOUT"):
            continue
        if "." in conn_blob and re.search(r"\.\w+\(", conn_blob):
            mapping = dict(re.findall(r"\.(\w+)\s*\(\s*([^)]*?)\s*\)", conn_blob))
            mapping = {k.lower(): v.strip() for k, v in mapping.items()}
            output_names = ("q", "qn", "y", "yn", "z", "zn", "o", "out", "x")
            output_key = next((name for name in output_names if mapping.get(name)), None)
            output = mapping.get(output_key, "") if output_key else ""
            # Liberty pin names vary (A1/A2, IN1/IN2, CLK, RESET_B, ...).
            # Treat all named pins except the allowlisted output pin as inputs;
            # this preserves real net connectivity without cell-library-specific art.
            inps = []
            for key, signal in mapping.items():
                if key == output_key or not signal or signal in inps:
                    continue
                inps.append(signal)
        else:
            conns = [t.strip() for t in re.split(r",\s*(?![^()]*\))", conn_blob)]
            output = conns[0] if conns else ""
            inps = conns[1:] if len(conns) > 1 else []
        instances.append({"cell": cell_type, "name": inst_name, "short": short,
                         "output": output, "inputs": inps})

    # 信号驱动表（含总线基名兜底）
    sig_driver = {}
    for inst in instances:
        if inst["output"]:
            sig_driver[inst["output"]] = inst["name"]
            base = _base(inst["output"])
            if base != inst["output"] and base not in sig_driver:
                sig_driver[base] = inst["name"]

    sname = lambda n: re.sub(r"^[\\$]+", "", n).replace("\\", "").split("[")[0]

    # 高亮集：实例名列表
    hl_set = set(highlight_path or [])

    # ── Graphviz 有向图 ──
    dot = Digraph(name=module_name, format="svg", graph_attr={
        "rankdir": "LR", "bgcolor": "#f8f9fa",
        "fontname": "sans-serif", "fontsize": "11",
        "label": f"{module_name}  —  {len(instances)} gates, {len(inputs)} inputs, {len(outputs)} outputs"
                 + (f"  |  🌟 最长路径 {len(hl_set)} 个门" if hl_set else ""),
        "labeljust": "c", "labelloc": "t",
        "pad": "0.3", "nodesep": "0.25", "ranksep": "0.5",
        "splines": "ortho", "overlap": "false",
    })

    COLORS = {
        "DFF":  ("#bf8700", "#fff8f0"), "AND":   ("#0969da", "#ddf4ff"),
        "NAND": ("#0969da", "#ddf4ff"), "OR":    ("#1a7f37", "#dafbe1"),
        "NOR":  ("#1a7f37", "#dafbe1"), "XOR":   ("#9a6700", "#fff8c5"),
        "XNOR": ("#9a6700", "#fff8c5"), "NOT":   ("#cf222e", "#ffebe9"),
        "BUF":  ("#656d76", "#f6f8fa"), "MUX":   ("#8250df", "#fbefff"),
    }
    HL_COLOR = "#0969da"  # 高亮蓝色

    # 输入端口
    with dot.subgraph(name="cluster_inputs") as sg:
        sg.attr(label="Inputs", fontsize="10", fontcolor="#0969da",
                style="dashed", color="#d0d7de")
        for p in inputs:
            sg.node(f"in_{p}", label=p, shape="box", style="filled,rounded",
                    fillcolor="#ddf4ff", color="#0969da", fontcolor="#0969da", fontsize="9")

    # 门节点
    for inst in instances:
        nid = f"g_{sname(inst['name'])}"
        is_hl = inst["name"] in hl_set
        border, bg = COLORS.get(inst["short"], ("#656d76", "#f6f8fa"))
        if is_hl:
            border, bg = HL_COLOR, "#ddf4ff"  # 高亮蓝
        is_dff = any(d in inst["short"] for d in ("DFF", "LATCH"))
        shape = "box" if is_dff else (
            "invhouse" if inst["short"] in ("AND", "NAND", "ANDNOT") else (
            "house" if inst["short"] in ("OR", "NOR", "ORNOT") else (
            "triangle" if inst["short"] == "NOT" else (
            "trapezium" if inst["short"] in ("MUX",) else "box"))))
        penwidth = "2.0" if is_hl else "1.0"
        dot.node(nid, label=inst["short"], shape=shape, style="filled,rounded",
                 fillcolor=bg, color=border, fontcolor=border, fontsize="8",
                 penwidth=penwidth,
                 tooltip=f"{'🌟 ' if is_hl else ''}{inst['name']} ({inst['cell']})")

    # 输出端口
    with dot.subgraph(name="cluster_outputs") as sg:
        sg.attr(label="Outputs", fontsize="10", fontcolor="#bf8700",
                style="dashed", color="#d0d7de")
        for p in outputs:
            sg.node(f"out_{p}", label=p, shape="box", style="filled,rounded",
                    fillcolor="#fff8f0", color="#bf8700", fontcolor="#bf8700", fontsize="9")

    # ── 连线 ──
    for inst in instances:
        dst = f"g_{sname(inst['name'])}"
        is_hl_edge = inst["name"] in hl_set
        for inp in inst["inputs"]:
            if not inp:
                continue
            base = _base(inp)
            if inp in inputs or base in inputs:
                src = f"in_{base}"
            elif inp in sig_driver:
                src = f"g_{sname(sig_driver[inp])}"
            elif base in sig_driver:
                src = f"g_{sname(sig_driver[base])}"
            else:
                continue
            color = HL_COLOR if (is_hl_edge and src.startswith("g_")) else "#656d76"
            penw = "1.2" if is_hl_edge else "0.5"
            dot.edge(src, dst, arrowhead="dot", arrowsize="0.4",
                     color=color, penwidth=penw)

    # 输出连线
    for inst in instances:
        out_sig = inst["output"]
        base = _base(out_sig)
        if out_sig in outputs or base in outputs:
            target_port = out_sig if out_sig in outputs else base
            is_hl = inst["name"] in hl_set
            dot.edge(f"g_{sname(inst['name'])}", f"out_{target_port}",
                     arrowhead="dot", arrowsize="0.4",
                     color=HL_COLOR if is_hl else "#656d76",
                     penwidth="1.2" if is_hl else "0.5")

    svg_bytes = dot.pipe(format="svg")
    return svg_bytes.decode("utf-8") if isinstance(svg_bytes, bytes) else svg_bytes


def main(argv=None):
    ap = argparse.ArgumentParser(description="从门级网表生成电路框图 SVG（带门符号）")
    ap.add_argument("netlist", type=Path, help="门级网表 .v 文件")
    ap.add_argument("-o", "--output", type=Path, help="输出 SVG 路径（默认 stdout）")
    ap.add_argument("--schematic", action="store_true", help="生成带连线的原理图（默认：门库存图）")
    a = ap.parse_args(argv)

    if not a.netlist.is_file():
        print(f"错误：文件不存在 {a.netlist}", file=sys.stderr)
        return 1

    text = a.netlist.read_text(encoding="utf-8", errors="replace")
    if a.schematic:
        svg = generate_schematic_svg(text)
    else:
        data = parse_ports_and_gates(text)
        svg = generate_svg(data)

    if a.output:
        a.output.write_text(svg, encoding="utf-8")
        print(f"已写入 {a.output} ({a.output.stat().st_size / 1024:.0f} KB)", file=sys.stderr)
    else:
        print(svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
