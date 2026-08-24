#!/usr/bin/env python3
"""Generate the reviewed AgenticEDA opening-report figures.

The visual language follows the two AgenticPD reference diagrams supplied in
``plan/文档``: large numbered lanes, restrained pastel fills, explicit function
and evidence labels, orthogonal arrows, and a shared audit strip.  The SVGs are
deterministic and every figure also gets an editable draw.io source and a
Mermaid source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


OUT = Path("/share/home/yuanwenjie/Desktop/AgenticEDA_开题报告_四轮审稿版_图片")
OUT.mkdir(parents=True, exist_ok=True)

INK = "#172033"
MUTED = "#637083"
LINE = "#7d8998"
PANEL = "#fbfcfe"
COLORS = {
    "blue": ("#f1f5ff", "#6d7fa8"),
    "mint": ("#effaf7", "#559987"),
    "lav": ("#f5f1ff", "#7e70a8"),
    "sand": ("#fff8e8", "#b19a55"),
    "rose": ("#fff2f4", "#b77a83"),
    "gray": ("#f6f8fb", "#8d98a7"),
}


def txt(x: float, y: float, lines: list[str] | tuple[str, ...], *, size=16,
        weight=400, anchor="middle", color=INK, line=1.34, cls="") -> str:
    body = "".join(
        f'<tspan x="{x}" dy="{0 if i == 0 else size * line}">{escape(value)}</tspan>'
        for i, value in enumerate(lines)
    )
    return (f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}" '
            f'font-size="{size}" font-weight="{weight}" fill="{color}">{body}</text>')


def defs() -> str:
    return """
<defs>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
    <feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#28384f" flood-opacity="0.10"/>
  </filter>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8.5" refY="5" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L10,5 L0,10 Z" fill="#536579"/>
  </marker>
  <marker id="arrow-green" markerWidth="10" markerHeight="10" refX="8.5" refY="5" orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L10,5 L0,10 Z" fill="#498c7c"/>
  </marker>
</defs>
"""


def header(title: str, subtitle: str, width: int) -> str:
    return (txt(60, 54, [title], size=31, weight=750, anchor="start")
            + txt(60, 84, [subtitle], size=15, anchor="start", color=MUTED))


def lane(x: int, y: int, w: int, h: int, number: str, title: str,
         subtitle: str = "", tone="gray") -> str:
    fill, stroke = COLORS[tone]
    body = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" '
            f'fill="{fill}" fill-opacity="0.62" stroke="{stroke}" stroke-opacity="0.38"/>'
            + txt(x + 25, y + 31, [f"{number} · {title.upper()}"], size=14,
                  weight=700, anchor="start", color="#4f5d6d"))
    if subtitle:
        body += txt(x + 25, y + 56, [subtitle], size=13, anchor="start", color=MUTED)
    return body


def card(x: int, y: int, w: int, h: int, title: str, lines: list[str],
         tone="blue", *, title_size=17, text_size=13, shadow=True,
         badge: str | None = None) -> str:
    fill, stroke = COLORS[tone]
    filt = ' filter="url(#shadow)"' if shadow else ""
    result = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="13" '
              f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{filt}/>'
              + txt(x + w / 2, y + 35, [title], size=title_size, weight=720))
    if badge:
        result += (f'<circle cx="{x + 22}" cy="{y + 22}" r="12" fill="{stroke}"/>'
                   + txt(x + 22, y + 27, [badge], size=12, weight=750, color="white"))
    if lines:
        result += txt(x + w / 2, y + 63, lines, size=text_size, color=MUTED)
    return result


def pill(x: int, y: int, w: int, text_value: str, tone="mint") -> str:
    fill, stroke = COLORS[tone]
    return (f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="17" fill="{fill}" stroke="{stroke}"/>'
            + txt(x + w / 2, y + 23, [text_value], size=12, weight=650))


def arrow(x1: int, y1: int, x2: int, y2: int, *, label="", dashed=False,
          green=False, bend: tuple[int, int] | None = None) -> str:
    color = "#498c7c" if green else "#536579"
    marker = "arrow-green" if green else "arrow"
    dash = ' stroke-dasharray="8 6"' if dashed else ""
    if bend:
        bx, by = bend
        path = f"M{x1},{y1} L{bx},{by} L{x2},{y2}"
        lx, ly = bx, by - 8
    else:
        path = f"M{x1},{y1} L{x2},{y2}"
        lx, ly = (x1 + x2) / 2, (y1 + y2) / 2 - 8
    out = (f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.4" '
           f'marker-end="url(#{marker})"{dash}/>' )
    if label:
        out += txt(lx, ly, [label], size=12, weight=600, color=color)
    return out


def footer(y: int, text_value: str, width: int) -> str:
    return (f'<line x1="60" y1="{y}" x2="{width - 60}" y2="{y}" stroke="#d6dde7"/>'
            + txt(65, y + 31, [text_value], size=13, weight=650, anchor="start", color="#435065"))


def svg(title: str, subtitle: str, body: str, *, width=1800, height=1080,
        caption="") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">{defs()}'
            '<rect width="100%" height="100%" fill="#ffffff"/>'
            '<style>text{font-family:"Noto Sans CJK SC","Microsoft YaHei",Arial,sans-serif}</style>'
            + header(title, subtitle, width) + body + footer(height - 62, caption, width) + '</svg>')


@dataclass
class DNode:
    key: str
    label: str
    x: int
    y: int
    w: int
    h: int
    tone: str = "blue"


@dataclass
class DPanel:
    key: str
    label: str
    x: int
    y: int
    w: int
    h: int
    tone: str = "gray"


@dataclass
class DSpec:
    title: str
    nodes: list[DNode]
    edges: list[tuple[str, str, str]]
    panels: list[DPanel] = field(default_factory=list)
    width: int = 1800
    height: int = 1080


def drawio(spec: DSpec, number: int) -> str:
    mx = ET.Element("mxfile", host="app.diagrams.net", agent="AgenticEDA-reviewed")
    dia = ET.SubElement(mx, "diagram", name=f"Figure {number}", id=f"agenticeda-reviewed-{number}")
    model = ET.SubElement(dia, "mxGraphModel", dx=str(spec.width), dy=str(spec.height), grid="1",
                          gridSize="10", page="1", pageWidth=str(spec.width), pageHeight=str(spec.height))
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    title = ET.SubElement(root, "mxCell", id="title", value=spec.title,
                          style="text;html=1;strokeColor=none;fillColor=none;fontSize=28;fontStyle=1;align=left;",
                          vertex="1", parent="1")
    ET.SubElement(title, "mxGeometry", x="50", y="20", width=str(spec.width - 100), height="50", **{"as": "geometry"})
    for panel in spec.panels:
        fill, stroke = COLORS[panel.tone]
        cell = ET.SubElement(root, "mxCell", id=f"panel-{panel.key}", value=panel.label,
                             style=(f"rounded=1;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingTop=12;spacingLeft=14;"
                                    f"fillColor={fill};strokeColor={stroke};opacity=55;fontSize=13;fontStyle=1;"),
                             vertex="1", parent="1")
        ET.SubElement(cell, "mxGeometry", x=str(panel.x), y=str(panel.y), width=str(panel.w), height=str(panel.h), **{"as": "geometry"})
    for node in spec.nodes:
        fill, stroke = COLORS[node.tone]
        cell = ET.SubElement(root, "mxCell", id=node.key, value=node.label.replace("\n", "<br>"),
                             style=(f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
                                    "fontSize=13;fontStyle=1;shadow=1;spacing=8;"), vertex="1", parent="1")
        ET.SubElement(cell, "mxGeometry", x=str(node.x), y=str(node.y), width=str(node.w), height=str(node.h), **{"as": "geometry"})
    for i, (source, target, label) in enumerate(spec.edges):
        cell = ET.SubElement(root, "mxCell", id=f"edge-{i}", value=label,
                             style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeWidth=2;fontSize=11;",
                             edge="1", parent="1", source=source, target=target)
        ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
    return ET.tostring(mx, encoding="unicode", xml_declaration=True)


def fig1() -> tuple[str, DSpec]:
    b = lane(40, 110, 1720, 205, "01", "SPEC → VERIFIED RTL",
             "写RTL与写测试分离；RTLScout内部是候选生成—固定评估—反馈修补循环", "blue")
    nodes = [
        (70, 175, 210, "自然语言需求", ["功能/接口/时钟/复位", "约束、PPA偏好、歧义"], "blue"),
        (330, 175, 210, "Spec Agent", ["解析为SpecIR", "生成验收条件与问题单"], "lav"),
        (590, 175, 220, "Verification Agent", ["独立生成TB/SVA/计划", "冻结内容哈希与来源"], "lav"),
        (860, 175, 220, "RTLScout内部循环", ["计划→编辑→evaluate", "读诊断→最小修补/分支"], "sand"),
        (1130, 175, 250, "Protected evaluator", ["compile/lint/sim/mutation", "功能通过后才测cost"], "rose"),
        (1430, 175, 260, "可晋级RTL候选", ["candidate lineage + SHA", "送OpenROAD重新测PPA"], "mint"),
    ]
    for i, (x, y, w, t, ls, tone) in enumerate(nodes):
        b += card(x, y, w, 105, t, ls, tone, text_size=12, badge=str(i + 1))
        if i < len(nodes) - 1:
            b += arrow(x + w, 228, nodes[i + 1][0], 228)
    b += lane(40, 340, 1720, 220, "02", "OPENROAD BASELINE + EDA→AI",
              "Runtime执行完整flow；原始artifact是权威，EDAIR/CircuitOps提供可查询视图", "mint")
    mid = [
        (90, "锁定实验合同", ["RTL/PDK/tool commit", "目标/预算/真实flow seed"], "blue"),
        (390, "Baseline重复运行", ["synth→place→CTS→route", "成功/失败/耗时全记录"], "sand"),
        (690, "Artifact Registry", ["log/report/netlist/DEF/GDS", "SHA-256 + parser provenance"], "rose"),
        (990, "Typed EDAIR", ["metric/path/instance/net", "unknown与loss_manifest"], "mint"),
        (1290, "EvidencePacket", ["按问题选事实与子图", "不足时按artifact回读"], "lav"),
    ]
    for i, (x, t, ls, tone) in enumerate(mid):
        b += card(x, 410, 230, 110, t, ls, tone, text_size=12)
        if i < 4:
            b += arrow(x + 230, 465, mid[i + 1][0], 465)
    b += lane(40, 585, 1720, 315, "03", "SEARCH → STALL → REDIRECT → LEARN",
              "BO只提出候选；QoR来自真实运行；经验需受控实验和留出设计复验", "lav")
    bot = [
        (70, 665, 210, "BO/GP组合提案", ["每目标RBF GP预测 μ/σ", "加权EI×经验可行率"], "lav"),
        (330, 665, 220, "Runtime批次", ["每配置重复3次", "median/IQR/range/failure"], "sand"),
        (600, 665, 220, "Review门", ["硬约束→统计改善", "更新best verified utility"], "rose"),
        (870, 665, 220, "停滞判定", ["连续3 batch低于阈值", "冻结trace并生成诊断包"], "rose"),
        (1140, 665, 220, "Repair Agent", ["定位stage/root cause", "只提白名单ActionSpec"], "sand"),
        (1410, 665, 280, "复测与知识准入", ["局部干预→holdout", "validated/refuted/negative"], "mint"),
    ]
    for i, (x, y, w, t, ls, tone) in enumerate(bot):
        b += card(x, y, w, 115, t, ls, tone, text_size=12)
        if i < 5:
            b += arrow(x + w, 723, bot[i + 1][0], 723)
    # Feedback routes travel below the lane and re-enter from the left.  They
    # must not run through the BO card or look like forward execution edges.
    b += '<path d="M440,780 L440,815 L45,815 L45,723 L70,723" fill="none" stroke="#498c7c" stroke-width="2.4" marker-end="url(#arrow-green)" stroke-dasharray="8 6"/>'
    b += txt(255, 807, ["未停滞：下一批组合"], size=12, weight=600, color="#498c7c")
    b += '<path d="M1550,780 L1550,865 L25,865 L25,748 L70,748" fill="none" stroke="#498c7c" stroke-width="2.4" marker-end="url(#arrow-green)" stroke-dasharray="8 6"/>'
    b += txt(940, 857, ["验证后的正/负经验影响下一轮提案"], size=12, weight=600, color="#498c7c")
    b += pill(230, 925, 1340, "Agent思考链：地图 → 语义 → 实验 → 假设 → 实现 → 验证 → 审查 → 记忆", "gray")
    b += pill(1325, 302, 400, "最终目标架构；实现状态见报告证据表", "gray")
    pic = svg("AgenticEDA：从自然语言到可审计的自演化闭环",
              "最终目标架构；模型负责理解与提案，Runtime和固定评估器负责执行与判定。",
              b, height=1040,
              caption="Figure 1 · 每次判断都能回到run_id、artifact SHA和验收合同；任何Agent都不能自行宣布正确或优化。")
    ds = DSpec("AgenticEDA：从自然语言到可审计的自演化闭环", [], [], [
        DPanel("p1", "01 · SPEC → VERIFIED RTL", 40, 110, 1720, 205, "blue"),
        DPanel("p2", "02 · OPENROAD BASELINE + EDA→AI", 40, 340, 1720, 220, "mint"),
        DPanel("p3", "03 · SEARCH → STALL → REDIRECT → LEARN", 40, 585, 1720, 315, "lav"),
    ], height=1040)
    dnodes = [
        DNode("n1", "自然语言需求\n功能/接口/约束", 70, 175, 210, 105), DNode("n2", "Spec Agent\nSpecIR/验收条件", 330, 175, 210, 105, "lav"),
        DNode("n3", "Verification Agent\nTB/SVA/哈希冻结", 590, 175, 220, 105, "lav"), DNode("n4", "RTLScout内部循环\n计划/编辑/评估/修补", 860, 175, 220, 105, "sand"),
        DNode("n5", "Protected evaluator\ncompile/lint/sim/mutation", 1130, 175, 250, 105, "rose"), DNode("n6", "可晋级RTL\nlineage/SHA/PPA重测", 1430, 175, 260, 105, "mint"),
        DNode("n7", "锁定合同", 90, 410, 230, 110), DNode("n8", "Baseline重复运行", 390, 410, 230, 110, "sand"), DNode("n9", "Artifact Registry", 690, 410, 230, 110, "rose"),
        DNode("n10", "Typed EDAIR", 990, 410, 230, 110, "mint"), DNode("n11", "EvidencePacket", 1290, 410, 230, 110, "lav"),
        DNode("n12", "BO/GP组合提案", 70, 665, 210, 115, "lav"), DNode("n13", "Runtime每配置重复3次", 330, 665, 220, 115, "sand"),
        DNode("n14", "Review硬约束/统计", 600, 665, 220, 115, "rose"), DNode("n15", "连续3批停滞", 870, 665, 220, 115, "rose"),
        DNode("n16", "Repair Agent\n白名单ActionSpec", 1140, 665, 220, 115, "sand"), DNode("n17", "复测与知识准入", 1410, 665, 280, 115, "mint"),
    ]
    ds.nodes = dnodes
    ds.edges = [(f"n{i}", f"n{i+1}", "") for i in range(1, 6)] + [(f"n{i}", f"n{i+1}", "") for i in range(7, 11)] + [(f"n{i}", f"n{i+1}", "") for i in range(12, 17)] + [("n17", "n12", "validated evidence")]
    return pic, ds


def fig2() -> tuple[str, DSpec]:
    b = lane(40, 110, 1720, 200, "01", "EVIDENCE CREATION",
             "不把聊天总结当知识；先把运行事实、原始文件和重复统计固定下来", "blue")
    top = [(80, "Runtime终态", ["run/stage/attempt/status", "失败、超时也保留"], "blue"),
           (410, "Artifact登记", ["log/report/netlist/DEF", "SHA、工具commit、source span"], "rose"),
           (740, "EDAIR证据", ["指标/路径/实例/网/违例", "loss_manifest列出未展开内容"], "mint"),
           (1070, "重复统计", ["按真实seed聚合", "median/IQR/range/failure"], "lav"),
           (1400, "Observation卡", ["只陈述观测", "不自动写成因果规律"], "sand")]
    for i, (x, t, ls, tone) in enumerate(top):
        b += card(x, 170, 250, 105, t, ls, tone, text_size=12)
        if i < 4: b += arrow(x + 250, 223, top[i + 1][0], 223)
    b += lane(40, 335, 1720, 295, "02", "HYPOTHESIS → CONTROLLED INTERVENTION",
              "先写可证伪假设，再运行2×2组合实验；主效应和交互效应必须分开", "sand")
    b += card(80, 420, 260, 130, "机制假设", ["claim + context fingerprint", "mechanism + falsifier", "execution_allowed = false"], "sand", text_size=12)
    b += card(405, 400, 300, 170, "预注册实验合同", ["参数A低/高 × 参数B低/高", "每角真实独立seed ≥ 2", "指标、硬约束、停止规则", "分析方法与效应阈值先固定"], "rose", text_size=12)
    b += card(770, 400, 300, 170, "差分中的差分", ["主效应A、主效应B", "interaction = ΔB|A高 − ΔB|A低", "报告方差/CI，不以非零即成立"], "lav", text_size=12)
    b += card(1135, 400, 260, 170, "本设计判断", ["supported / refuted", "只在当前RTL/PDK/tool上下文", "不能立即自动执行"], "blue", text_size=12)
    b += card(1460, 420, 230, 130, "Holdout任务", ["冻结同一干预合同", "换未见设计复验"], "mint", text_size=12)
    for x1, x2 in [(340,405),(705,770),(1070,1135),(1395,1460)]: b += arrow(x1, 485, x2, 485)
    b += lane(40, 655, 1720, 270, "03", "HOLDOUT GATE + KNOWLEDGE LIFECYCLE",
              "复现才扩大适用范围；反向或不稳定结果写成负迁移证据，而不是删除", "mint")
    b += card(90, 735, 250, 120, "留出设计复验", ["同PDK/tool/parser", "同参数水平与真实seed"], "blue", text_size=12)
    b += card(410, 715, 250, 160, "准入判定", ["方向 + 效应量 + CI", "硬约束与失败率", "协议hash与run证据齐全"], "rose", text_size=12)
    b += card(750, 690, 300, 95, "复现：Validated condition", ["扩大到两个指定fingerprint；仍不称普适规律"], "mint", text_size=12)
    b += card(750, 810, 300, 95, "未复现：Negative transfer", ["action_eligible=false；收窄边界并记录反例"], "rose", text_size=12)
    b += card(1140, 715, 250, 160, "证据知识卡", ["context/claim/mechanism", "intervention/falsifier/CI", "run IDs + artifact SHA"], "lav", text_size=12)
    b += card(1470, 735, 220, 120, "Planner检索", ["先context过滤", "只把卡片当建议"], "sand", text_size=12)
    b += arrow(340, 795, 410, 795) + arrow(660, 765, 750, 738) + arrow(660, 825, 750, 858)
    b += arrow(1050, 738, 1140, 760) + arrow(1050, 858, 1140, 830) + arrow(1390, 795, 1470, 795)
    b += arrow(1580, 855, 205, 275, label="下一次真实运行产生新证据，更新/退休旧卡片", dashed=True, green=True, bend=(205, 900))
    b += pill(1180, 875, 510, "当前证据：单一负迁移门控演示，不是四臂学习消融", "rose")
    pic = svg("自演化不是“保存成功案例”：证据如何变成可复验知识",
              "三道门：运行事实可追溯、局部干预可证伪、跨设计复验后才扩大适用范围。", b,
              height=1020, caption="Figure 2 · 当前GCD→FIFO交互方向反转，因此知识卡被标为refuted且不可执行；这正是负迁移门发挥作用。")
    ds = DSpec("自演化：证据如何变成可复验知识", [
        DNode("a","Runtime终态",80,170,250,105),DNode("b","Artifact + SHA",410,170,250,105,"rose"),DNode("c","EDAIR证据",740,170,250,105,"mint"),DNode("d","真实seed统计",1070,170,250,105,"lav"),DNode("e","Observation卡",1400,170,250,105,"sand"),
        DNode("f","机制假设\nclaim/context/falsifier",80,420,260,130,"sand"),DNode("g","预注册2×2实验合同",405,400,300,170,"rose"),DNode("h","DiD主效应/交互+CI",770,400,300,170,"lav"),DNode("i","当前设计局部判断",1135,400,260,170),DNode("j","Holdout任务",1460,420,230,130,"mint"),
        DNode("k","留出设计复验",90,735,250,120),DNode("l","准入判定",410,715,250,160,"rose"),DNode("m","Validated condition",750,690,300,95,"mint"),DNode("n","Negative transfer",750,810,300,95,"rose"),DNode("o","证据知识卡",1140,715,250,160,"lav"),DNode("p","Planner检索",1470,735,220,120,"sand")
    ], [], [DPanel("p1","01 · EVIDENCE CREATION",40,110,1720,200,"blue"),DPanel("p2","02 · HYPOTHESIS → CONTROLLED INTERVENTION",40,335,1720,295,"sand"),DPanel("p3","03 · HOLDOUT GATE + KNOWLEDGE LIFECYCLE",40,655,1720,270,"mint")], height=1020)
    ds.edges=[("a","b",""),("b","c",""),("c","d",""),("d","e",""),("f","g",""),("g","h",""),("h","i",""),("i","j",""),("k","l",""),("l","m","replicated"),("l","n","refuted"),("m","o",""),("n","o",""),("o","p","")]
    return pic, ds


def fig3() -> tuple[str, DSpec]:
    b = lane(40, 110, 1720, 190, "01", "AUTHORITATIVE RAW LAYER",
             "所有压缩视图都能回到原文件；未解析、截断和单位不明必须显式标注", "blue")
    raws=[(75,"日志/报告",["metrics.json · timing · route","stdout/stderr · stage status"],"blue"),(390,"设计/约束",["RTL/netlist · SDC · Liberty","module/cell/pin/net"],"lav"),(705,"物理数据",["DEF/ODB/GDS/SPEF","坐标/层/几何/寄生"],"mint"),(1020,"运行上下文",["PDK/tool commit/flow seed","stage/attempt/timeout"],"sand"),(1335,"Artifact Registry",["SHA-256/size/MIME","parser version/source span"],"rose")]
    for i,(x,t,ls,tone) in enumerate(raws):
        b+=card(x,170,240 if i<4 else 350,95,t,ls,tone,text_size=11)
        if i<4:b+=arrow(x+(240 if i<4 else 350),218,raws[i+1][0],218)
    b += lane(40, 325, 1720, 280, "02", "PARSERS → TYPED IR → RELATION GRAPH",
              "专用parser保留单位和provenance；unknown不填默认值；逻辑网与物理网必须分名", "mint")
    b += card(75,405,250,135,"Parser adapters",["OpenROAD metric/stage JSON","OpenSTA timing blocks","DEF components/netlist graph"],"sand",text_size=12)
    b += card(385,385,310,175,"EDAIR envelope",["Run/Stage/Event/Metric","TimingPath/Point","PhysicalInstance/Violation","每个对象回指artifact + span"],"mint",text_size=12)
    b += card(755,385,310,175,"CircuitOps关系表",["cell/pin/net property tables","pin-pin · cell-pin · net-pin edges","稳定主键/row count/table hash"],"lav",text_size=12)
    b += card(1125,385,260,175,"Fidelity manifest",["parser_fidelity.unparsed_blocks","loss_manifest列packet省略","逻辑/物理来源类型分开"],"rose",text_size=12)
    b += card(1445,405,245,135,"可重建性",["按artifact+range回读", "抽样与原报告/DEF核对"],"blue",text_size=12)
    for x1,x2 in [(325,385),(695,755),(1065,1125),(1385,1445)]: b+=arrow(x1,472,x2,472)
    b += lane(40, 630, 1720, 285, "03", "BOUNDED QUERY FOR AGENTS",
              "不是把整份18GB日志塞进prompt，而是分辨率逐级提升，并让遗漏可见", "lav")
    q=[(75,"Level 0 · KPI",["12项左右全局指标","用于筛选，不用于根因判断"],"gray"),(385,"Level 1 · Stage",["阶段事件/失败/趋势","定位synth/place/CTS/route"],"blue"),(695,"Level 2 · Object",["timing path/instance/net子图","跨artifact实体连接"],"mint"),(1005,"Level 3 · Excerpt",["原文行范围+hash核验","用于审查parser和长尾细节"],"sand"),(1315,"EvidencePacket",["按问题排序最多N条事实","loss + directory + query trace"],"rose")]
    for i,(x,t,ls,tone) in enumerate(q):
        b+=card(x,710,240 if i<4 else 365,120,t,ls,tone,text_size=11)
        if i<4:b+=arrow(x+(240 if i<4 else 365),770,q[i+1][0],770)
    b += pill(320,855,1160,"权限边界：Agent可读证据与受限查询；只有Runtime能执行ActionSpec", "gray")
    pic=svg("EDA数据如何转换为AI可读证据：原始层、结构层、查询层同时存在",
            "低失真不等于零压缩；它要求每次压缩都声明损失，并能按稳定ID回读原始证据。",b,height=1010,
            caption="Figure 3 · 8月25日四设计EDAIR已导出对象和provenance；当前消融仅证明‘结构更多’，尚未证明parser完全忠实或诊断更准确。")
    ds=DSpec("EDA数据如何转换为AI可读证据",[
        DNode("a","日志/报告",75,170,240,95),DNode("b","设计/约束",390,170,240,95,"lav"),DNode("c","物理数据",705,170,240,95,"mint"),DNode("d","运行上下文",1020,170,240,95,"sand"),DNode("e","Artifact Registry",1335,170,350,95,"rose"),
        DNode("f","Parser adapters",75,405,250,135,"sand"),DNode("g","EDAIR typed objects",385,385,310,175,"mint"),DNode("h","CircuitOps relation tables",755,385,310,175,"lav"),DNode("i","Fidelity/loss manifest",1125,385,260,175,"rose"),DNode("j","hash-checked回读",1445,405,245,135),
        DNode("k","L0 KPI",75,710,240,120,"gray"),DNode("l","L1 Stage",385,710,240,120),DNode("m","L2 Object graph",695,710,240,120,"mint"),DNode("n","L3 Raw excerpt",1005,710,240,120,"sand"),DNode("o","EvidencePacket",1315,710,365,120,"rose")
    ],[],[DPanel("p1","01 · AUTHORITATIVE RAW LAYER",40,110,1720,190,"blue"),DPanel("p2","02 · PARSERS → TYPED IR → RELATION GRAPH",40,325,1720,280,"mint"),DPanel("p3","03 · BOUNDED QUERY FOR AGENTS",40,630,1720,285,"lav")],height=1010)
    ds.edges=[("a","b",""),("b","c",""),("c","d",""),("d","e",""),("f","g",""),("g","h",""),("h","i",""),("i","j",""),("k","l",""),("l","m",""),("m","n",""),("n","o","")]
    return pic,ds


def fig4() -> tuple[str, DSpec]:
    b=lane(40,110,1720,195,"01","SPEC CONTRACT + INDEPENDENT TEST AUTHORING","写答案的人不能同时控制判分规则；自动测试不等于人工测试", "blue")
    top=[(75,"自然语言",["功能、接口、时序语义","约束与PPA偏好"],"blue"),(365,"SpecIR合同",["top/ports/width/reset","acceptance/assumptions/questions"],"lav"),(655,"Verification Agent",["reference model/scoreboard","directed+random tests/SVA plan"],"mint"),(985,"结构预检",["DUT实例/时钟推进","失败路径/PASS标记/可编译"],"sand"),(1275,"冻结VerificationPackage",["TB/SVA/seed/coverage plan","content SHA + provenance"],"rose")]
    for i,(x,t,ls,tone) in enumerate(top):
        w=240 if i<4 else 410
        b+=card(x,170,w,100,t,ls,tone,text_size=11)
        if i<4:b+=arrow(x+w,220,top[i+1][0],220)
    b+=lane(40,330,1720,380,"02","RTLSCOUT CANDIDATE EVOLUTION — INTERNAL LOOP","RTLScout是受限搜索控制器：管理候选、调用固定评估器、读取反馈；不是单次LLM生成", "sand")
    steps=[(75,"A · 初始化",["SpecIR+冻结验证包","elite/parent+workspace","max_steps/token budget"],"blue"),(350,"B · 计划/编辑",["read/list/create/replace","一次只改候选RTL","禁止改TB和evaluator"],"sand"),(625,"C · Compile/Lint",["Verilator syntax/elaboration","Yosys read/check/synth","失败→错误位置/类型"],"rose"),(900,"D · Functional",["冻结TB simulation","SVA/CEC按条件启用","失败→trace给RTL作者"],"rose"),(1175,"E · TB强度",["mutation killed/executable","coverage targets","弱TB→Verification Agent"],"rose"),(1450,"F · Cost/Select",["仅PASS候选排名","Yosys/ABC cost","更优→archive+lineage"],"mint")]
    for i,(x,t,ls,tone) in enumerate(steps):
        b+=card(x,430,225,145,t,ls,tone,text_size=11)
        if i<5:b+=arrow(x+225,502,steps[i+1][0],502)
    b+=arrow(1010,575,463,620,label="RTL功能错误：返回RTLScout修补",dashed=True,bend=(1010,620))
    b+=arrow(1287,430,775,385,label="TB杀不死变异：返回Verifier补测试并重新冻结",dashed=True,bend=(1287,385))
    b+=arrow(1562,575,463,655,label="成本未改善：保留parent，产生下一候选",dashed=True,green=True,bend=(1562,655))
    b+=lane(40,735,1720,190,"03","PROMOTION + REAL EVIDENCE","前端正确性门与后端PPA门分开；固定四题是回归集，不是用户输入白名单", "mint")
    b+=card(80,800,280,85,"RTLCandidate登记",["RTL SHA / parent / model / steps"],"lav",text_size=11)
    b+=card(430,800,280,85,"独立Runtime复验",["compile + sim + mutation receipts"],"rose",text_size=11)
    b+=card(780,800,280,85,"OpenROAD baseline",["真实synth/place/CTS/route/GDS"],"sand",text_size=11)
    b+=card(1130,800,280,85,"候选PPA对照",["同PDK/tool/seed协议/重复统计"],"blue",text_size=11)
    b+=card(1480,800,210,85,"晋级或拒绝",["证据全链归档"],"mint",text_size=11)
    for x1,x2 in [(360,430),(710,780),(1060,1130),(1410,1480)]:b+=arrow(x1,842,x2,842)
    pic=svg("从Spec到经验证RTL：拆开RTLScout、Testbench与固定评估器",
            "Verification Agent自动写测试；RTLScout只写/演化RTL；固定工具链负责判定，三者角色不能混。",b,height=1020,
            caption="Figure 4 · 四题单seed已从自然语言跑到GDS；它证明固定suite可行性，不证明任意spec泛化或多seed成功率。")
    ds=DSpec("从Spec到经验证RTL",[
        DNode("a","自然语言",75,170,240,100),DNode("b","SpecIR合同",365,170,240,100,"lav"),DNode("c","Verification Agent",655,170,240,100,"mint"),DNode("d","TB结构预检",985,170,240,100,"sand"),DNode("e","冻结VerificationPackage",1275,170,410,100,"rose"),
        DNode("f","A 初始化",75,430,225,145),DNode("g","B 计划/受限编辑",350,430,225,145,"sand"),DNode("h","C Compile/Lint",625,430,225,145,"rose"),DNode("i","D Functional",900,430,225,145,"rose"),DNode("j","E TB强度",1175,430,225,145,"rose"),DNode("k","F Cost/Select",1450,430,225,145,"mint"),
        DNode("l","RTLCandidate",80,800,280,85,"lav"),DNode("m","独立Runtime复验",430,800,280,85,"rose"),DNode("n","OpenROAD baseline",780,800,280,85,"sand"),DNode("o","PPA对照",1130,800,280,85),DNode("p","晋级/拒绝",1480,800,210,85,"mint")
    ],[],[DPanel("p1","01 · SPEC CONTRACT + INDEPENDENT TEST AUTHORING",40,110,1720,195,"blue"),DPanel("p2","02 · RTLSCOUT CANDIDATE EVOLUTION — INTERNAL LOOP",40,330,1720,380,"sand"),DPanel("p3","03 · PROMOTION + REAL EVIDENCE",40,735,1720,190,"mint")],height=1020)
    ds.edges=[("a","b",""),("b","c",""),("c","d",""),("d","e",""),("f","g",""),("g","h",""),("h","i",""),("i","j",""),("j","k",""),("i","g","RTL fail"),("j","c","weak TB"),("k","g","not better"),("l","m",""),("m","n",""),("n","o",""),("o","p","")]
    return pic,ds


def fig5() -> tuple[str, DSpec]:
    b=lane(40,110,1120,800,"01","EVIDENCE KNOWLEDGE CARD — VERSIONED SCHEMA","一条卡片同时保存事实、机制、反证条件、适用边界和证据指针", "blue")
    fields=[
        (80,180,500,115,"A · Context key",["design_fingerprint / PDK / tool commit / stage","parser_version / objective_profile / seed protocol"],"blue"),
        (620,180,500,115,"B · Observation statistics",["baseline/candidate raw values · median/IQR/range/failure","timing path/DRC/congestion objects + provenance"],"mint"),
        (80,330,500,130,"C · Claim + mechanism",["可复用结论是什么？为什么可能发生？","claim_scope必须小于等于证据覆盖范围","相关性不得直接升级为因果"],"sand"),
        (620,330,500,130,"D · Intervention + stopping",["变量水平/控制变量/预算/真实seed","预期效应方向与最小效应阈值","硬约束和停止规则"],"lav"),
        (80,495,500,130,"E · Falsifier + uncertainty",["什么结果会推翻？CI/方差/未观测数据","OOD条件、可迁移边界、已知反例","invalid/timeout不得静默删除"],"rose"),
        (620,495,500,130,"F · Evidence pointers",["run_id / artifact SHA / source span / protocol hash","source与holdout实验ID、不可变时间戳","parser与代码commit"],"blue"),
        (80,660,500,130,"G · Lifecycle status",["draft → tested → supported/refuted","holdout_validated / negative_transfer / retired","action_eligible与execution_allowed分开"],"mint"),
        (620,660,500,130,"H · Retrieval + authority",["先context硬过滤，再做BM25词项排序","当前没有向量索引；不得写成vector RAG","卡片只能建议，Runtime才执行"],"sand"),
    ]
    for x,y,w,h,t,ls,tone in fields:b+=card(x,y,w,h,t,ls,tone,text_size=12)
    b+=lane(1200,110,560,800,"02","REAL EXAMPLE: GCD → FIFO HOLDOUT","这张卡为什么被拒绝，而不是被写成“成功经验”", "rose")
    b+=card(1240,180,480,120,"局部观测 · GCD",["core_utilization × place_density","instance area DiD interaction = +1.862","4角 × 每角3次重复；WNS/DRC门通过"],"sand",text_size=12)
    b+=card(1240,335,480,120,"预注册迁移 · FIFO",["相同两参数水平、相同上下文fingerprint","holdout interaction = −1.596","效应方向反转"],"blue",text_size=12)
    b+=card(1240,490,480,145,"准入结论 · REFUTED",["promoted = false","action_eligible = false","teacher guidance: 不得复用为通用规则","下一步：真实独立seed + 第三设计"],"rose",text_size=12)
    b+=card(1240,675,480,115,"审稿人注意",["当前三次replica的实测值完全相同，方差为0","只有一个source→holdout设计对，不能称普适因果","价值在于系统保存了反例并阻止负迁移"],"lav",text_size=12)
    b+=arrow(1480,300,1480,335)+arrow(1480,455,1480,490)+arrow(1480,635,1480,675)
    b+=pill(1240,835,480,"单一负迁移门控演示；不是四臂学习消融结果", "rose")
    pic=svg("知识卡片长什么样：不是一句经验，而是一份可审计实验记录",
            "左侧是完整schema；右侧用8月25日真实GCD→FIFO反例说明知识状态如何变化。",b,height=1010,
            caption="Figure 5 · 失败经验没有被删除：它被保存为负迁移证据，用于阻止下一轮盲目复用同一组合规则。")
    ds=DSpec("知识卡片长什么样",[
        DNode("a","A Context key",80,180,500,115),DNode("b","B Observation statistics",620,180,500,115,"mint"),DNode("c","C Claim + mechanism",80,330,500,130,"sand"),DNode("d","D Intervention + stopping",620,330,500,130,"lav"),DNode("e","E Falsifier + uncertainty",80,495,500,130,"rose"),DNode("f","F Evidence pointers",620,495,500,130),DNode("g","G Lifecycle status",80,660,500,130,"mint"),DNode("h","H Retrieval + authority",620,660,500,130,"sand"),
        DNode("i","GCD interaction +1.862",1240,180,480,120,"sand"),DNode("j","FIFO holdout −1.596",1240,335,480,120),DNode("k","REFUTED / not action eligible",1240,490,480,145,"rose"),DNode("l","方法学限制：伪重复/无CI",1240,675,480,115,"lav")
    ],[("i","j","holdout"),("j","k","opposite direction"),("k","l","audit")],[DPanel("p1","01 · EVIDENCE KNOWLEDGE CARD — VERSIONED SCHEMA",40,110,1120,800,"blue"),DPanel("p2","02 · REAL EXAMPLE: GCD → FIFO HOLDOUT",1200,110,560,800,"rose")],height=1010)
    return pic,ds


def fig6() -> tuple[str, DSpec]:
    b=lane(40,110,1720,315,"01","ONE BAYESIAN-OPTIMIZATION ITERATION","GP不优化芯片；它只根据已测数据决定下一组最值得交给OpenROAD测的组合", "blue")
    alg=[(65,"观测矩阵",["X: 连续参数组合","Y: area/timing/power","失败只训练可行率"],"blue"),(345,"重复点聚合",["同组合取均值","sample variance / replica数","参数映射到[0,1]"],"lav"),(625,"每目标一个GP",["固定RBF length=0.35","Cholesky求解","预测 μᵢ(x), σᵢ(x)"],"mint"),(905,"加权EI",["目标方向统一+偏好权重","EI平衡探索与利用","乘局部Beta平滑可行率"],"sand"),(1185,"选择组合",["512点Latin hypercube","去掉已测点","argmax acquisition"],"rose"),(1465,"OpenROAD实测",["同组合重复3次","完整finish + hard gates","结果回填观测库"],"mint")]
    for i,(x,t,ls,tone) in enumerate(alg):
        b+=card(x,200,230,145,t,ls,tone,text_size=11)
        if i<5:b+=arrow(x+230,272,alg[i+1][0],272)
    b+=arrow(1580,345,740,345,label="更新观测：先按同配置聚合均值/方差，再拟合GP",dashed=True,green=True,bend=(1580,385))
    b+=lane(40,450,1080,455,"02","CAMPAIGN CONTROL + STALL REDIRECTION","同预算比较anytime曲线；连续3批不改善才换方向", "mint")
    b+=card(75,535,250,130,"Baseline R次",["共同起点/同flow seed表","median/IQR/failure","建立约束与归一化基准"],"blue",text_size=12)
    b+=card(390,535,250,130,"候选batch",["BO与random同run预算","并行执行但随机化顺序","每配置按同一合同重复3次"],"sand",text_size=12)
    b+=card(705,515,250,170,"Review",["非法/溢出/DRC/timing硬拒绝","按预设权重算verified utility","更新best-so-far与停滞计数","不以模型预测替代实测"],"rose",text_size=11)
    b+=card(390,735,250,115,"有可靠改善",["更新best verified utility","继续局部+全局探索"],"mint",text_size=12)
    b+=card(705,735,250,115,"连续3批停滞",["冻结trace/模型/候选","生成stage diagnosis packet"],"rose",text_size=12)
    b+=arrow(325,600,390,600)+arrow(640,600,705,600)+arrow(830,685,515,735,label="improved",green=True,bend=(830,700))+arrow(830,685,830,735,label="stalled")
    b+=arrow(515,735,515,700,dashed=True,green=True)+arrow(955,792,1075,792,label="交Repair Agent换参数子空间/动作类型")
    b+=lane(1160,450,600,455,"03","8月25日证据与边界","真实运行很多，不等于统计设计已经正确", "rose")
    b+=card(1200,530,520,115,"已执行的等预算工作量（非有效重复）",["4 designs × 3 optimizer labels × 每策略每格12 runs","BO 144 + Random 144 full-flow runs","阈值事件：BO 7/12，Random 4/12"],"mint",text_size=12)
    b+=card(1200,680,520,150,"结论边界",["按design中位数胜负：BO 2，Random 2","optimizer首个seed未严格配对","replica未传不同ORFS flow seed，IQR=0是伪重复","GP虽聚合重复点，伪重复仍使估计方差为0"],"rose",text_size=12)
    b+=pill(1260,855,400,"只可写描述性结果；不可写普遍或显著优势", "lav")
    pic=svg("BO与GP如何开展组合参数探索：算法、实验合同和停滞换向",
            "当前实现：重复点噪声聚合、每目标固定RBF GP、加权EI与经验可行率；QoR只认OpenROAD实测。",b,height=1010,
            caption="Figure 6 · BO阈值命中7/12、Random为4/12，但design中位数胜负2:2；不能写成普遍或显著优势。")
    ds=DSpec("BO与GP如何开展组合参数探索",[
        DNode("a","观测矩阵 X,Y",65,200,230,145),DNode("b","重复点均值与均值方差",345,200,230,145,"lav"),DNode("c","每目标RBF GP\nμ(x), σ(x)",625,200,230,145,"mint"),DNode("d","scalarized EI × feasibility",905,200,230,145,"sand"),DNode("e","512 LHS池 argmax",1185,200,230,145,"rose"),DNode("f","OpenROAD重复实测",1465,200,230,145,"mint"),
        DNode("g","共同Baseline",75,535,250,130),DNode("h","同预算候选batch",390,535,250,130,"sand"),DNode("i","Review hard gates/utility",705,515,250,170,"rose"),DNode("j","可靠改善",390,735,250,115,"mint"),DNode("k","连续3批停滞",705,735,250,115,"rose"),
        DNode("l","阈值事件 BO 7/12 / Random 4/12",1200,530,520,115,"mint"),DNode("m","design胜负2:2；无显著性结论",1200,680,520,150,"rose")
    ],[("a","b",""),("b","c",""),("c","d",""),("d","e",""),("e","f",""),("f","c","aggregate mean/variance"),("g","h",""),("h","i",""),("i","j","improved"),("i","k","stalled"),("l","m","audit")],[DPanel("p1","01 · ONE BAYESIAN-OPTIMIZATION ITERATION",40,110,1720,315,"blue"),DPanel("p2","02 · CAMPAIGN CONTROL + STALL REDIRECTION",40,450,1080,455,"mint"),DPanel("p3","03 · 8月25日证据与边界",1160,450,600,455,"rose")],height=1010)
    return pic,ds


MERMAID = {
1: '''%%{init: {"theme":"base","themeVariables":{"fontFamily":"Noto Sans CJK SC, Microsoft YaHei, Arial","primaryColor":"#f1f5ff","primaryBorderColor":"#6d7fa8","lineColor":"#536579"},"flowchart":{"curve":"linear","htmlLabels":true}}}%%
flowchart TB
 subgraph R["01 Spec 到经验证 RTL"]
  direction LR
  U["自然语言需求"] --> S["Spec Agent<br/>SpecIR 与验收条件"]
  S --> V["Verification Agent<br/>TB SVA 计划与哈希冻结"]
  S --> T["RTLScout<br/>计划 编辑 评估 修补"]
  V --> E["Protected evaluator<br/>compile lint sim mutation"]
  T --> E
  E -->|通过| C["RTL候选 lineage 与 SHA"]
  E -->|RTL失败| T
  E -->|TB弱| V
 end
 subgraph D["02 OpenROAD 与 EDA 到 AI"]
  direction LR
  C --> B["Baseline 多seed full flow"] --> A["Artifact Registry"] --> I["EDAIR 与 CircuitOps"] --> P["EvidencePacket"]
 end
 subgraph L["03 搜索 停滞 换向 学习"]
  direction LR
  P --> G["BO GP组合提案"] --> X["Runtime真实实验"] --> J["Review硬约束与统计"]
  J --> Q{"连续3批无可靠改善"}
  Q -->|否| G
  Q -->|是| H["Repair Agent诊断"] --> W["白名单ActionSpec"] --> K["复测 Holdout 知识准入"] --> G
 end''',
2: '''flowchart TB
 subgraph E["运行证据"]
  direction LR
  A["Runtime终态"] --> B["Artifact SHA与provenance"] --> C["EDAIR事实"] --> D["真实seed统计"] --> O["Observation卡"]
 end
 subgraph H["可证伪假设与局部干预"]
  direction LR
  O --> F["claim context mechanism falsifier"] --> G["预注册2×2组合实验"] --> I["主效应 交互效应 CI"] --> J["当前设计局部判断"]
 end
 subgraph V["留出设计与知识准入"]
  direction LR
  J --> K["未见设计holdout"] --> L{"方向 效应量 CI 硬约束"}
  L -->|复现| M["Validated condition"]
  L -->|未复现| N["Negative transfer"]
  M --> P["Evidence Knowledge Card"]
  N --> P
  P --> Q["context过滤检索"] --> A
 end''',
3: '''flowchart TB
 subgraph A["01 权威原始层"]
  direction LR
  L["OpenROAD日志 报告"] --> R["Artifact Registry<br/>SHA parser version source span"]
  N["RTL netlist SDC Liberty"] --> R
  D["DEF ODB GDS SPEF"] --> R
  T["tool commit flow seed stage"] --> R
 end
 subgraph B["02 Parser与结构视图"]
  direction LR
  R --> P["专用Parser adapters"] --> I["Typed EDAIR"]
  P --> G["CircuitOps关系表"]
  I --> F["fidelity 与 loss manifest"]
  G --> F
 end
 subgraph C["03 Agent分级查询"]
  direction LR
  F --> Q["L0 KPI"] --> S["L1 Stage"] --> O["L2 Object graph"] --> X["L3 raw excerpt"] --> E["EvidencePacket"]
 end
 E -->|证据不足 按artifact回读| R''',
4: '''flowchart TB
 subgraph T["自动测试作者 与 RTL作者分离"]
  direction LR
  A["自然语言"] --> B["SpecIR合同"] --> C["Verification Agent"] --> D["TB结构预检"] --> E["冻结VerificationPackage"]
 end
 subgraph R["RTLScout候选演化内部循环"]
  direction LR
  B --> F["初始化parent与workspace"] --> G["计划与受限RTL编辑"] --> H["Verilator Yosys compile lint"] --> I["冻结TB functional simulation"] --> J["mutation coverage质量门"] --> K["Yosys ABC cost与选择"]
  H -->|语法结构失败| G
  I -->|RTL功能失败| G
  J -->|测试太弱| C
  K -->|成本未改善| G
 end
 subgraph P["晋级与后端证据"]
  direction LR
  K --> L["RTLCandidate lineage SHA"] --> M["Runtime独立复验"] --> N["OpenROAD baseline"] --> O["同协议PPA对照"] --> Q["晋级或拒绝"]
 end''',
5: '''flowchart TB
 subgraph K["Evidence Knowledge Card"]
  direction LR
  A["Context key"] --> B["Observation statistics"] --> C["Claim mechanism"] --> D["Intervention stopping"]
  D --> E["Falsifier uncertainty"] --> F["run artifact protocol SHA"] --> G["Lifecycle status"] --> H["Retrieval authority"]
 end
 subgraph X["真实反例"]
  direction LR
  I["GCD interaction +1.862"] --> J["FIFO holdout -1.596"] --> L["REFUTED<br/>action eligible false"] --> M["negative transfer evidence"]
 end
 H -."context filtered advice".-> X''',
6: '''flowchart TB
 subgraph A["一次BO迭代"]
  direction LR
  X["观测矩阵 X Y"] --> N["同组合均值与均值方差<br/>参数映射到0到1"] --> G["每目标固定RBF GP<br/>预测均值μ与方差σ"] --> E["加权scalarized EI<br/>乘经验可行率"] --> C["512点LHS池<br/>argmax未测组合"] --> R["OpenROAD同组合重复3次"] --> S["按配置聚合均值方差"] --> G
 end
 subgraph B["Campaign与停滞换向"]
  direction LR
  D["共同Baseline"] --> P["BO Random同预算batch"] --> V["硬约束与verified utility"] --> Q{"连续3批无可靠改善"}
  Q -->|否| P
  Q -->|是| H["冻结trace与stage diagnosis"] --> W["Repair Agent换子空间或动作类型"]
 end
 subgraph Z["当前证据边界"]
  direction LR
  T["288 full-flow runs<br/>阈值事件 BO 7/12 Random 4/12"] --> U["design中位数胜负2比2<br/>不可写普遍或显著优势"]
 end
 S -."实验结果进入campaign审查".-> D
 W -."审计后更新证据边界".-> T'''
}


def main() -> None:
    figures = [fig1, fig2, fig3, fig4, fig5, fig6]
    for index, builder in enumerate(figures, 1):
        picture, spec = builder()
        (OUT / f"{index:02d}_drawio_style.svg").write_text(picture, encoding="utf-8")
        (OUT / f"{index:02d}_editable.drawio").write_text(drawio(spec, index), encoding="utf-8")
        (OUT / f"{index:02d}_mermaid.mmd").write_text(MERMAID[index] + "\n", encoding="utf-8")
        (OUT / f"{index:02d}_mermaid_render.html").write_text(
            "<!doctype html><meta charset='utf-8'><style>body{margin:0;padding:28px;background:white;}"
            ".mermaid{font-family:'Noto Sans CJK SC','Microsoft YaHei',Arial,sans-serif;}</style>"
            "<pre class='mermaid'>" + html.escape(MERMAID[index]) + "</pre>"
            "<script src='mermaid.min.js'></script><script>"
            "mermaid.initialize({startOnLoad:true,theme:'base',flowchart:{curve:'linear',htmlLabels:true}});"
            "</script>", encoding="utf-8")
    (OUT / "README.md").write_text(
        "# OpenROAD AgenticEDA v2 验收报告图片\n\n"
        "每个编号包含：`*_drawio_style.svg`（报告用科研图）、`*_editable.drawio`（可编辑源）、"
        "`*_mermaid.mmd`（Mermaid源）、`*_mermaid_rendered.png`（本地Mermaid渲染）和`*_preview.png`（DOCX用预览）。\n\n"
        "六图依次为：总工作流、自演化知识链、EDA-to-AI接口、RTL生成链、知识卡片、BO/GP参数探索。\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
