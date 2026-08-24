#!/usr/bin/env python3
"""Generate deterministic, publication-style AgenticEDA research figures.

The figures deliberately expose algorithms, evidence contracts and control
boundaries instead of hiding them behind product names.
"""
from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

OUT = Path("/share/home/yuanwenjie/Desktop/AgenticEDA_开题报告_图片_终稿")
OUT.mkdir(parents=True, exist_ok=True)

PALETTE = {
    "blue": ("#eef5ff", "#2f64ad"), "green": ("#edf8f2", "#357b58"),
    "orange": ("#fff5e9", "#d16b2c"), "red": ("#fff1f0", "#cf4b43"),
    "gray": ("#f6f8fb", "#728096"), "purple": ("#f4f0ff", "#7458b6"),
}


def text(x, y, lines, *, size=16, anchor="middle", weight="normal", color="#183153"):
    spans = "".join(
        f'<tspan x="{x}" dy="{0 if i == 0 else size * 1.35}">{escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    return f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" fill="{color}">{spans}</text>'


def box(x, y, w, h, title, lines, tone="blue", number=None, size=15):
    fill, stroke = PALETTE[tone]
    badge = ""
    if number is not None:
        badge = (f'<circle cx="{x+20}" cy="{y+20}" r="13" fill="{stroke}"/>'
                 + text(x+20, y+25, [str(number)], size=13, weight="bold", color="white"))
    title_x = x + w / 2 + (8 if number is not None else 0)
    title_size = 14 if len(title) > 18 else 16
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
            + badge + text(title_x, y+30, [title], size=title_size, weight="bold")
            + text(title_x, y+58, lines, size=size, color="#334b68"))


def panel(x, y, w, h, title, tone="blue"):
    fill, stroke = PALETTE[tone]
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#ffffff" stroke="{stroke}" stroke-width="2"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="38" rx="10" fill="{stroke}"/>'
            + text(x+w/2, y+26, [title], size=19, weight="bold", color="white"))


def arrow(x1, y1, x2, y2, *, dashed=False, color="#4f6682", label=None):
    dash = ' stroke-dasharray="8 6"' if dashed else ""
    body = f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{color}" stroke-width="2.5" fill="none" marker-end="url(#arrow)"{dash}/>'
    if label:
        body += text((x1+x2)/2, (y1+y2)/2-7, [label], size=13, color=color)
    return body


def base(title, caption, body, height=900):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="{height}" viewBox="0 0 1600 {height}">
<defs><marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" viewBox="0 0 10 10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="#4f6682"/></marker></defs>
<rect width="1600" height="{height}" fill="white"/><style>text{{font-family:Arial,'Microsoft YaHei',sans-serif}}</style>
{text(45,45,[title],size=28,anchor='start',weight='bold')}{body}
<line x1="45" y1="{height-68}" x2="1555" y2="{height-68}" stroke="#b6c1cf"/>
{text(50,height-38,[caption],size=16,anchor='start',weight='bold',color='#1f2937')}</svg>'''


def figure1():
    b = panel(35,75,1530,330,"Level 1 · Spec-to-Verified-RTL（前端生成闭环）","blue")
    xs = [60,290,520,750,980,1210]
    specs = [
        ("自然语言需求",["功能、接口、时钟","PPA偏好与约束"],"blue"),
        ("Spec Agent",["抽取端口/位宽","生成验收条件","列出歧义与假设"],"green"),
        ("Verification Agent",["独立生成Testbench","断言/覆盖计划","内容哈希冻结"],"green"),
        ("RTLScout ReAct",["受限文件工具","生成/修改RTL","读取真实评估反馈"],"orange"),
        ("固定评估器",["RTL错→反馈RTLScout","TB弱→反馈Verifier","Yosys/ABC实测cost"],"red"),
        ("候选档案",["correctness first","更低cost才晋级","保留父子lineage"],"purple"),
    ]
    for i,(t,ls,c) in enumerate(specs):
        b += box(xs[i],125,195,200,t,ls,c,i+1,14)
        if i < len(xs)-1: b += arrow(xs[i]+195,225,xs[i+1],225)
    b += arrow(1075,325,847,350,dashed=True,label="失败/成本未改善：反馈下一步")
    b += panel(35,430,1530,365,"Level 2 · OpenROAD 自演化后端（数据—搜索—诊断—动作—学习）","green")
    xs2=[60,270,480,690,900,1110,1320]
    specs2=[("Baseline",["锁定RTL/PDK","工具版本/seed","重复R次"],"blue"),
            ("EDA→AI接口",["原始artifact+SHA","EDAIR对象视图","CircuitOps关系表"],"blue"),
            ("BO/GP",["每目标一个GP","均值+方差","EI选组合候选"],"green"),
            ("Runtime实验",["并行受限执行","记录失败/超时","QoR重复统计"],"orange"),
            ("停滞检测",["best-so-far曲线","连续3轮无改善","触发换向"],"red"),
            ("Repair Agent",["定位stage/root cause","提出白名单动作","不直接改源码"],"orange"),
            ("审查与记忆",["复测/holdout","事实+机制+反例","知识卡片入库"],"purple")]
    for i,(t,ls,c) in enumerate(specs2):
        b += box(xs2[i],485,175,205,t,ls,c,i+1,13)
        if i < len(xs2)-1: b += arrow(xs2[i]+175,585,xs2[i+1],585)
    b += arrow(1405,690,568,745,dashed=True,label="证据反馈下一轮提案")
    b += box(330,715,960,55,"Agent reasoning chain",["地图 → 语义 → 实验 → 假设 → 实现 → 验证 → 审查 → 记忆"],"gray",None,14)
    return base("AgenticEDA 最终目标：从自然语言到可审计自演化闭环",
                "Figure 1. 两级闭环共享固定评估器和证据契约；模型不能自行宣告正确或QoR改善。",b)


def figure2():
    b=panel(35,75,1530,250,"A. 一次实验如何变成结构化证据","blue")
    xs=[65,315,565,815,1065,1315]
    data=[("Runtime终态",["run/stage/attempt","成功、失败、超时"],"blue"),("Artifact注册",["日志/报告/网表","SHA-256不可变引用"],"blue"),("统计归纳",["重复R次","median/IQR/failure"],"green"),("反思Agent",["提出机制与反例","不把相关性写成因果"],"orange"),("知识候选",["claim/context","intervention/falsifier"],"purple"),("准入状态",["hypothesis","negative evidence","validated rule"],"red")]
    for i,(t,ls,c) in enumerate(data):
        b+=box(xs[i],130,190,135,t,ls,c,i+1,13)
        if i<5:b+=arrow(xs[i]+190,198,xs[i+1],198)
    b+=panel(35,355,1530,380,"B. 知识准入不是“保存成功案例”，而是分级复验","green")
    b+=box(70,425,235,200,"局部受控实验",["2×2组合参数","每角≥2次重复","同PDK/工具/阶段"],"blue",1)
    b+=box(365,425,235,200,"差分中的差分",["估计主效应","估计interaction","记录置信与失败"],"green",2)
    b+=box(660,425,235,200,"留出设计复验",["新RTL fingerprint","同干预水平","预注册验收门"],"orange",3)
    b+=box(955,400,255,110,"复现",["晋级：replicated condition","仍限定上下文"],"green",4)
    b+=box(955,545,255,110,"不复现",["写入negative transfer","收窄假设边界"],"red",4)
    b+=box(1270,425,240,200,"检索供下一轮",["先按context过滤","再做BM25式排序","只读建议，不直接执行"],"purple",5)
    b+=arrow(305,525,365,525)+arrow(600,525,660,525)+arrow(895,525,955,455)+arrow(895,525,955,600)+arrow(1210,455,1270,500)+arrow(1210,600,1270,550)
    b+=arrow(1390,625,190,690,dashed=True,label="复验后的正/负经验共同影响后续实验")
    return base("自演化：从运行事实到可复验知识，而不是日志收藏",
                "Figure 2. 只有带上下文、证据哈希和holdout结果的经验才能升级；失败与反例同样入库。",b,840)


def figure3():
    b=panel(35,75,1530,300,"A. 权威数据层：保留全部原始细节","blue")
    for i,(x,t,ls,c) in enumerate([(65,"OpenROAD reports",["metrics.json","timing/route reports"],"blue"),(320,"Netlist/SDC",["cell/pin/net","clock/constraint"],"blue"),(575,"DEF/ODB/GDS",["placement/routing","geometry/layer"],"blue"),(830,"Runtime metadata",["tool commit/seed","stage/attempt/status"],"blue")]):
        b+=box(x,145,210,145,t,ls,c,i+1)
    b+=box(1110,130,390,175,"Artifact Registry",["每个文件记录SHA-256、大小、类型、解析器版本","原文件始终是最终权威；规范化视图可重建"],"red",5,14)
    # Independent orthogonal routes: input boxes join a provenance bus below
    # the cards, then enter the artifact registry without crossing any box.
    for x in [170,425,680,935]:
        b += f'<path d="M{x},290 L{x},330" stroke="#4f6682" stroke-width="2" fill="none"/>'
    b += '<path d="M170,330 L1040,330" stroke="#4f6682" stroke-width="2" fill="none"/>'
    b += arrow(1040,330,1110,260,label="register")
    b+=panel(35,405,1530,330,"B. AI查询层：结构化对象 + 明示损失 + 按需回读","green")
    b+=box(65,470,240,190,"Parser adapters",["stage JSON parser","timing/physical parser","netlist→CircuitOps exporter"],"orange",1,13)
    b+=box(365,455,250,220,"EDAIR envelope",["DesignIR/RunEvidenceIR","TimingIR/PhysicalIR","每个对象回指artifact","unknown保持为空"],"green",2,13)
    b+=box(675,455,250,220,"CircuitOps tables",["cell/pin/net properties","五类edge关系","整表hash+row count","有界关系查询"],"green",3,13)
    b+=box(985,455,250,220,"Evidence packet",["按诊断目标排序","最多N条事实","loss_manifest列遗漏","artifact_directory可回读"],"purple",4,13)
    b+=box(1295,470,220,190,"Agent context",["先读关键事实","不足时按ID查询","不可把未展开当不存在"],"blue",5,13)
    b+=arrow(305,565,365,565)+arrow(615,530,675,530)+arrow(615,600,675,600)+arrow(925,565,985,565)+arrow(1235,565,1295,565)
    return base("EDA-to-AI 数据接口：低失真不是把所有日志塞进上下文",
                "Figure 3. 原始文件、结构化IR和Agent证据包三层并存；loss manifest使压缩造成的信息损失可见。",b,840)


def figure4():
    b=panel(35,75,1530,245,"A. 双Agent职责分离：写答案的人不能写判分规则","blue")
    b+=box(65,130,245,140,"SpecIR合同",["top/port/width","clock/reset/constraint","acceptance criteria"],"blue",1,13)
    b+=box(380,130,250,140,"Verification Agent",["只生成Testbench/SVA","列assumption/open question","输出覆盖计划"],"green",2,13)
    b+=box(700,130,250,140,"冻结VerificationPackage",["TB内容哈希","compile checks","oracle provenance"],"red",3,13)
    b+=box(1020,130,250,140,"RTLScout Agent",["只编辑候选RTL","不能修改oracle","受max_steps限制"],"orange",4,13)
    b+=box(1340,130,180,140,"Candidate lineage",["parent→child","generator/model","RTL artifact SHA"],"purple",5,13)
    b+=arrow(310,200,380,200)+arrow(630,200,700,200)+arrow(310,180,1020,180)+arrow(1270,200,1340,200)
    b+=panel(35,350,1530,410,"B. Protected evaluator：区分“RTL错误”和“Testbench太弱”","green")
    xs=[55,260,465,670,875,1080,1285]
    ds=[("Prompt+workspace",["Spec/工具说明","起点/elite seed","禁止shell越权"],"blue"),("LLM file tools",["create/replace/diff","read/list","提交evaluate"],"orange"),("Compile gate",["语法/elaboration","Verilator lint","Yosys check"],"red"),("Functional sim",["冻结TB/scoreboard","SVA/CEC可选","RTL错返回trace"],"red"),("TB quality gate",["mutation score","coverage targets","弱TB退回Verifier"],"red"),("Cost evaluator",["Yosys/ABC cells","transistors/wires","后端PPA重新测"],"green"),("Selection/archive",["仅PASS可排名","lower cost更新best","保留全部轨迹"],"purple")]
    for i,(t,ls,c) in enumerate(ds):
        b+=box(xs[i],415,175,190,t,ls,c,i+1,12)
        if i<6:b+=arrow(xs[i]+175,510,xs[i+1],510)
    # RTL compile/simulation failures return to the RTL author.
    b += '<path d="M845,605 L845,635 L347,635 L347,605" stroke="#cf4b43" stroke-width="2.2" fill="none" marker-end="url(#arrow)" stroke-dasharray="8 6"/>'
    b += text(600,630,["RTL错误：结构化trace返回RTLScout"],size=12,color="#a63d36")
    # A weak testbench returns to the verification agent and must be re-frozen.
    b += '<path d="M962,415 L962,335 L505,335 L505,270" stroke="#cf4b43" stroke-width="2.2" fill="none" marker-end="url(#arrow)" stroke-dasharray="8 6"/>'
    b += text(740,328,["mutation/coverage不足：补测试并重新冻结"],size=12,color="#a63d36")
    b+=box(585,675,430,55,"晋级OpenROAD条件",["同一candidate通过强制门；PPA由后端重新测量"],"gray",None,12)
    return base("RTL生成链路：把RTLScout拆成可检查的生成—评估—选择循环",
                "Figure 4. Testbench自动生成但独立冻结；RTLScout是候选优化循环，不是一个神秘的“生成RTL”黑盒。",b,850)


def figure5():
    b=panel(60,80,1480,680,"Evidence Knowledge Card · 一条可复用经验的完整数据结构","blue")
    b+=box(95,145,420,115,"1. Context key",["design fingerprint · PDK · toolchain commit · stage","metric parser version · objective profile"],"blue",1,14)
    b+=box(560,145,420,115,"2. Observation",["baseline/candidate重复值 · median/IQR · failure rate","最差timing path/DRC/拥塞证据引用"],"green",2,14)
    b+=box(1025,145,420,115,"3. Claim + mechanism",["局部结论是什么 · 为什么可能发生","禁止把相关性直接写成因果"],"orange",3,14)
    b+=box(95,310,420,140,"4. Intervention",["参数组合或白名单动作 · 控制变量","预期方向 · 运行预算 · 停止条件"],"orange",4,14)
    b+=box(560,310,420,140,"5. Falsifier + uncertainty",["什么结果会推翻假设 · 未观测数据","适用设计/工艺边界 · OOD标记"],"red",5,14)
    b+=box(1025,310,420,140,"6. Evidence pointers",["run_id · artifact SHA-256 · parser provenance","source/holdout实验ID · 不可变时间戳"],"blue",6,14)
    b+=box(95,500,420,140,"7. Validation status",["hypothesis → local intervention → holdout","validated / rejected / negative-transfer"],"purple",7,14)
    b+=box(560,500,420,140,"8. Retrieval policy",["先做严格context过滤 · 再做文本相关排序","action-eligible只允许verified事实/规则"],"green",8,14)
    b+=box(1025,500,420,140,"9. Authority",["知识卡只给建议，不直接执行","动作仍需提案—审查—Runtime—复测"],"red",9,14)
    return base("知识卡片：系统究竟“学到”并保存了什么",
                "Figure 5. 卡片不是一段总结文字，而是带上下文键、统计、反证条件和原始证据指针的版本化记录。",b,840)


def figure6():
    b=panel(35,75,1530,300,"A. 多目标Bayesian Optimization的每轮计算","blue")
    xs=[60,300,540,780,1020,1260]
    ds=[("观测矩阵",["X=参数组合","Y=area/timing/power","失败单独记录"],"blue"),("归一化",["参数映射[0,1]","目标按方向缩放","hard constraints过滤"],"blue"),("独立GP",["RBF kernel","Cholesky求解","预测μ(x), σ(x)"],"green"),("标量效用",["偏好权重w_i","方向统一","方差合成"],"green"),("Expected Improvement",["I=μ-best-ξ","EI=IΦ(z)+σφ(z)","探索与利用平衡"],"orange"),("候选计划",["池中argmax EI","去除已测点","写入预测与证据"],"purple")]
    for i,(t,ls,c) in enumerate(ds):
        b+=box(xs[i],135,195,175,t,ls,c,i+1,13)
        if i<5:b+=arrow(xs[i]+195,222,xs[i+1],222)
    b+=panel(35,405,1530,330,"B. 真实实验反馈与停滞换向","green")
    b+=box(70,470,230,185,"重复执行",["同一配置R次","固定PDK/tool版本","不同seed显式记录"],"orange",1,13)
    b+=box(360,470,230,185,"统计与约束",["median/IQR/range","失败率","溢出/非法硬拒绝"],"blue",2,13)
    b+=box(650,470,230,185,"Pareto/Best-so-far",["不以单次最好晋级","比较hypervolume","更新GP训练集"],"green",3,13)
    b+=box(940,455,240,100,"有改善",["候选成为新incumbent","继续局部/全局探索"],"green",4,13)
    b+=box(940,585,240,100,"连续3轮无改善",["冻结参数搜索轨迹","生成stage诊断包"],"red",4,13)
    b+=box(1240,470,255,185,"换向策略",["改变参数子空间/偏好","或交给Repair Agent","动作仍受白名单审查"],"purple",5,13)
    b+=arrow(300,562,360,562)+arrow(590,562,650,562)+arrow(880,562,940,505)+arrow(880,562,940,635)+arrow(1180,505,1240,530)+arrow(1180,635,1240,600)
    b+=arrow(1368,470,1368,365,dashed=True,label="实验结果回填下一轮")
    return base("BO/GP如何探索参数空间：模型、采集函数、重复测量与换向",
                "Figure 6. BO只提出下一组最值得测的参数；QoR必须由OpenROAD真实运行产生，GP预测不能当实验结果。",b,840)


FIGURES = [figure1, figure2, figure3, figure4, figure5, figure6]

MERMAID = {
1: """flowchart LR
  A[自然语言需求] --> B[Spec Agent: SpecIR]
  B --> C[Verification Agent: TB/SVA/coverage]
  C --> D[Frozen VerificationPackage]
  B --> E[RTLScout ReAct loop]
  D --> F[Fixed evaluator]
  E --> F
  F -->|fail + diagnostics| E
  F -->|correct + lower cost| G[Candidate lineage]
  G --> H[OpenROAD baseline]
  H --> I[EDAIR/CircuitOps]
  I --> J[Multi-objective BO/GP]
  J --> K[Repeated Runtime experiments]
  K --> L{3 rounds stalled?}
  L -->|no| J
  L -->|yes| M[Repair Agent diagnosis]
  M --> N[Allowlisted bounded action]
  N --> O[Re-evaluate / review / memory]
  O --> J""",
2: """flowchart TB
 A[Runtime terminal runs]-->B[Artifacts + SHA/provenance]-->C[Replication statistics]
 C-->D[Causal reflection: mechanism/falsifier]-->E[Hypothesis card]
 E-->F[Repeated 2x2 local intervention]-->G[Difference-in-differences]
 G-->H[Held-out design pre-registration]
 H--reproduced-->I[Replicated compound condition]
 H--not reproduced-->J[Negative-transfer evidence]
 I-->K[Context-filtered retrieval];J-->K;K-->L[Next bounded proposal];L-->A""",
3: """flowchart LR
 A[OpenROAD reports / netlist / DEF / ODB / GDS]-->B[Artifact registry: SHA + parser version]
 B-->C[EDAIR envelope]
 C-->D[TimingIR];C-->E[PhysicalIR];C-->F[RunEvidenceIR]
 B-->G[CircuitOps cell/pin/net tables]
 D-->H[Evidence packet];E-->H;F-->H;G-->H
 H-->I[loss_manifest + artifact directory]
 I-->J[LLM bounded query]
 J--insufficient evidence-->B""",
4: """flowchart LR
 A[SpecIR contract]-->B[Verification Agent]
 B-->C[Frozen TB/SVA/coverage]
 A-->D[RTLScout prompt/workspace]
 D-->E[LLM file-tool edit]
 E-->F[Verilator/Yosys compile gate]
 C-->G[Functional simulation/CEC]
 F-->G
 G--RTL fail diagnostics-->E
 G--RTL pass-->H[Mutation/coverage TB-quality gate]
 H--weak oracle-->B
 H--strong oracle-->I[Yosys/ABC cost]
 I--lower cost-->J[Best candidate + lineage]
 I--not lower-->E
 J-->K[OpenROAD promotion]""",
5: """flowchart TB
 A[Evidence Knowledge Card]-->B[Context key]
 A-->C[Repeated observation statistics]
 A-->D[Claim + mechanism]
 A-->E[Intervention + stopping rule]
 A-->F[Falsifier + uncertainty]
 A-->G[run/artifact SHA pointers]
 A-->H[holdout validation status]
 A-->I[retrieval + authority policy]""",
6: """flowchart LR
 A[Repeated observations X,Y]-->B[Normalize parameters/objectives]
 B-->C[One RBF Gaussian Process per objective]
 C-->D[Predict mean and variance]
 D-->E[Weighted utility + Expected Improvement]
 E-->F[argmax unseen feasible candidate]
 F-->G[Parallel OpenROAD runs]
 G-->H[median/IQR/failure/hard constraints]
 H-->C
 H-->I{3 rounds no improvement?}
 I--no-->E
 I--yes-->J[Freeze trace + stage diagnosis + redirect]""",
}


def drawio_xml(source: str, figure_number: int) -> str:
    """Create a genuinely editable mxGraph companion for each Mermaid graph."""
    nodes: dict[str, str] = {}
    for match in re.finditer(r"([A-Z])\[([^]]+)\]", source):
        key, label = match.group(1), match.group(2)
        nodes[key] = label.replace("<br/>", "\n")
    edges = []
    for line in source.splitlines():
        match = re.search(r"\b([A-Z])(?:--[^>]*-->|-->|---)([A-Z])\b", line.replace(" ", ""))
        if match:
            edges.append(match.groups())
    mxfile = ET.Element("mxfile", host="app.diagrams.net", agent="OpenROAD-AgenticEDA")
    diagram = ET.SubElement(mxfile, "diagram", name=f"Figure {figure_number}", id=f"agenticeda-{figure_number}")
    model = ET.SubElement(diagram, "mxGraphModel", dx="1600", dy="900", grid="1", gridSize="10",
                          page="1", pageWidth="1600", pageHeight="900", math="0", shadow="0")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    tones = ["dae8fc", "d5e8d4", "fff2cc", "f8cecc", "e1d5e7"]
    strokes = ["6c8ebf", "82b366", "d6b656", "b85450", "9673a6"]
    for index, (key, label) in enumerate(nodes.items()):
        col, row = index % 5, index // 5
        cell = ET.SubElement(root, "mxCell", id=f"n-{key}", value=label,
                             style=(f"rounded=1;whiteSpace=wrap;html=1;fillColor=#{tones[index%5]};"
                                    f"strokeColor=#{strokes[index%5]};fontSize=15;fontStyle=1;"),
                             vertex="1", parent="1")
        ET.SubElement(cell, "mxGeometry", x=str(45+col*300), y=str(90+row*190),
                      width="240", height="120", **{"as": "geometry"})
    for index, (src, dst) in enumerate(edges):
        if src not in nodes or dst not in nodes:
            continue
        cell = ET.SubElement(root, "mxCell", id=f"e-{index}",
                             style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeWidth=2;",
                             edge="1", parent="1", source=f"n-{src}", target=f"n-{dst}")
        ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
    return ET.tostring(mxfile, encoding="unicode", xml_declaration=True)


def main():
    for i, builder in enumerate(FIGURES, 1):
        (OUT / f"{i:02d}_drawio_style.svg").write_text(builder(), encoding="utf-8")
        (OUT / f"{i:02d}_mermaid.mmd").write_text(MERMAID[i] + "\n", encoding="utf-8")
        (OUT / f"{i:02d}_editable.drawio").write_text(drawio_xml(MERMAID[i], i), encoding="utf-8")


if __name__ == "__main__":
    main()
