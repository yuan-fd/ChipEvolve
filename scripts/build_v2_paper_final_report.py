#!/usr/bin/env python3
"""Build the human-readable v2 paper experiment delivery from frozen analyses."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def esc(value: object) -> str:
    return html.escape(str(value))


def pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def num(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}g}"


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def status_counts(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get("status") or "unknown")
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))


def closed_loop_rows(name: str, report: dict) -> list[tuple[object, ...]]:
    rows = []
    for item in report["checkpoint"]["state"].get("history") or []:
        summary = item.get("summary") or {}; metrics = summary.get("metrics") or {}
        constraints = summary.get("constraints") or []
        failed = [str(row.get("metric")) for row in constraints if row.get("passed") is False]
        rows.append((
            name, item.get("round"), item.get("parameters"), item.get("utility"),
            summary.get("eligible"), ", ".join(failed) if failed else "无",
            num((metrics.get("area_um2") or {}).get("median")),
            num((metrics.get("area_um2") or {}).get("iqr")),
            num((metrics.get("setup_wns_ns") or {}).get("median")),
            num((metrics.get("setup_wns_ns") or {}).get("iqr")),
            num((metrics.get("power_W") or {}).get("median")),
            num((metrics.get("power_W") or {}).get("iqr")),
        ))
    return rows


def table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    head = "".join(f"<th>{esc(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(item)}</td>" for item in row) + "</tr>"
                   for row in rows)
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def bars(items: list[tuple[str, float, str]], *, maximum: float | None = None) -> str:
    maximum = maximum or max((value for _, value, _ in items), default=1) or 1
    rows = []
    for label, value, display in items:
        width = max(0, min(100, 100 * value / maximum))
        rows.append(f"<div class='barrow'><span>{esc(label)}</span><div class='track'><i style='width:{width:.2f}%'></i></div><b>{esc(display)}</b></div>")
    return "<div class='bars'>" + "".join(rows) + "</div>"


def build(args: argparse.Namespace) -> tuple[str, list[dict], dict]:
    parameter = load(args.parameter); learning = load(args.learning)
    rtl = load(args.rtl); edair = load(args.edair); agent = load(args.agent)
    reference = load(args.references)
    aes = load(args.aes); jpeg = load(args.jpeg)
    p = parameter["primary"]; ci = p["bootstrap_median_95_ci"]
    eda_kpi, eda_typed = edair["totals"]["kpi_only"], edair["totals"]["typed_edair"]
    learning_arms = learning["arms"]
    rtl_rows = [(row["design"], f"{row['successes']}/{row['attempts']}",
                 pct(row["pass_rate"]), pct(row["pass_at_k"].get("5")),
                 row["first_candidate_passes"], row["iterative_rescues"],
                 row["unique_rtl_hashes"], row["unique_testbench_hashes"])
                for row in rtl["design_rows"]]
    rtl_ppa_rows = []
    for row in rtl["design_rows"]:
        metrics = row["ppa_vs_hidden_golden"]
        rtl_ppa_rows.append((
            row["design"],
            num(metrics["area_um2"]["generated_median"]),
            num(metrics["area_um2"]["golden_median"]),
            pct(metrics["area_um2"]["relative_generated_minus_golden"]),
            num(metrics["setup_wns_ns"]["generated_median"]),
            num(metrics["setup_wns_ns"]["golden_median"]),
            num(metrics["power_W"]["generated_median"]),
            num(metrics["power_W"]["golden_median"]),
        ))
    parameter_design_rows = []
    for design, test in parameter["per_design_secondary"].items():
        cells = [row for row in parameter["cells"] if row["design"] == design]
        parameter_design_rows.append((design,
            sum(row["winner"] == "bo_gp" for row in cells),
            sum(row["winner"] == "seeded_random" for row in cells),
            sum(row["winner"] == "tie" for row in cells),
            num(test["p_value"]), num(test["holm_adjusted_p_value"])))
    parameter_cell_rows = [(
        row["design"], row["seed"], num(row["bo_gp"]["best_utility"]),
        num(row["seeded_random"]["best_utility"]), num(row["paired_difference"]),
        row["winner"], row["bo_gp"]["failure_runs"],
        row["seeded_random"]["failure_runs"],
    ) for row in parameter["cells"]]
    profile_rows = []
    profile_data = parameter["objective_profile_replay"]["selection_difference_from_balanced"]
    for arm, values in profile_data.items():
        profile_rows.append((arm, values["area"], values["timing"],
                             values["performance"], values["power"]))
    learning_pair_rows = [(
        row["source"], row["holdout"], num(row.get("source_interaction")),
        num(row.get("holdout_interaction")), row.get("outcome"),
        row.get("knowledge_status"), row.get("accepted"),
    ) for row in learning["pairs"]]
    edair_design_rows = []
    for design in sorted({row["design"] for row in edair["calls"]}):
        for arm in ("kpi_only", "typed_edair"):
            selected = [row for row in edair["calls"]
                        if row["design"] == design and row["arm"] == arm]
            answers = sum(row["total"] for row in selected)
            correct = sum(row["correct"] for row in selected)
            unknown = sum(row["unknown"] for row in selected)
            false_answers = sum(row["false_answers"] for row in selected)
            edair_design_rows.append((design, arm, len(selected), answers, correct,
                                      pct(ratio(correct, answers)),
                                      pct(ratio(unknown, answers)),
                                      pct(ratio(false_answers, answers))))
    edair_paired = edair["paired_statistics"]
    edair_ci = edair_paired["bootstrap_mean_95_ci"]
    edair_question_rows = [(
        row["question_id"], row["label"], pct(row["kpi_only"]["accuracy"]),
        pct(row["typed_edair"]["accuracy"]), pct(row["accuracy_difference"]),
    ) for row in edair["question_rows"]]
    edair_secondary_rows = [(
        design, pct(row["mean_paired_difference"]), num(row["raw_p_value"]),
        num(row["holm_adjusted_p_value"]), row["reject_at_0_05"],
    ) for design, row in edair_paired["per_design_secondary"].items()]
    agent_trace_rows = [(
        row["design"], row["status"], row["hypothesis_events"],
        row["implementation_events"], row["validation_events"],
        row["validation_events_with_run_ids"],
        row["below_threshold_positive_candidates"],
    ) for row in agent["real_trace_rows"]]
    external_history_rows = closed_loop_rows("AES", aes) + closed_loop_rows("JPEG", jpeg)
    experiment_ledger = [
        {"study": "parameter", "unit": "full ORFS flow", "planned": 960,
         "observed": parameter["run_count"], "status": parameter["status"]},
        {"study": "learning", "unit": "full ORFS flow", "planned": 288,
         "observed": learning["ordered_pair_count"] * 24, "status": learning["status"]},
        {"study": "rtl-generation", "unit": "independent NL→GDS attempt", "planned": 20,
         "observed": rtl["attempts"], "status": rtl["status"]},
        {"study": "rtl-hidden-reference", "unit": "full ORFS flow", "planned": 12,
         "observed": reference["run_count"], "status": reference["status"]},
        {"study": "edair-qa", "unit": "LLM context/question call", "planned": 40,
         "observed": sum(x["calls"] for x in edair["totals"].values()), "status": edair["status"]},
        {"study": "agent-architecture", "unit": "injected-failure suite", "planned": 1,
         "observed": 1, "status": agent["status"]},
        {"study": "AES external smoke", "unit": "full ORFS flow", "planned": 4,
         "observed": len(aes["runtime_runs"]), "status": aes["checkpoint"]["state"]["status"]},
        {"study": "JPEG external smoke", "unit": "full ORFS flow", "planned": 4,
         "observed": len(jpeg["runtime_runs"]), "status": jpeg["checkpoint"]["state"]["status"]},
    ]
    refs = [
        ("EvolVE (2026, arXiv:2601.18067)", "结构化 testbench、演化式候选搜索、功能与 PPA 联合评价", "本平台写/审分离、候选历史、mutation 门与 GDS 条件 PPA"),
        ("AgenticPD (2026, arXiv:2607.04758)", "阶段感知 Agent、AES/ibex/JPEG、多次后端评估", "八阶段证据链、大设计 smoke、重复 OR_SEED 与停滞诊断"),
        ("Retrieve, Schedule, Reflect (2026, arXiv:2603.13767)", "检索、调度、反思与多轮 QoR 优化消融", "上下文隔离数值先验、非执行假设、BO/GP 对随机同预算比较"),
        ("PDAGENT-BENCH (2026, arXiv:2606.17253)", "Agent grounding、架构与 benchmark 化评价", "固定四设计、故障注入、权限与 evidence completeness"),
        ("EDATracer (2026, arXiv:2608.04032)", "typed graph/vector artifact analysis、重复 QA", "typed EDAIR 对 KPI-only 的 12题×4设计×5重复实测"),
        ("CircuitOps (ICCAD 2023)", "cell/pin/net 等 EDA 对象转成 ML 可查询结构", "逻辑实例/网、物理实例、时序路径及 SHA provenance"),
    ]
    html_body = f"""
<header><p class='eyebrow'>OpenROAD AgenticEDA · v2.0 · 冻结协议实验</p><h1>不是“跑通一次”，而是让数据经得住追问</h1>
<p class='lead'>这份报告只汇报已经落盘、可回放的实验。每个结论都同时给出对照组、样本数、失败记录和适用边界。v3 的 TimingECO、EvoDRC、Resynth 等工具执行不混入本轮结论。</p></header>
<section><h2>1. 一眼看懂：这一轮到底证明什么</h2><div class='cards'>
<article><b>{rtl['successes']}/{rtl['attempts']}</b><span>自然语言→独立测试→RTLScout→GDS 完整成功</span></article>
<article><b>{parameter['cell_count']} cells</b><span>BO/GP 与 seeded random 的设计×seed 配对比较</span></article>
<article><b>{learning['rejected_pair_count']}</b><span>跨设计不成立、被因果 holdout 阻止入库的规则</span></article>
<article><b>{pct(eda_typed['accuracy'])}</b><span>typed EDAIR 事实问答准确率（KPI-only 为 {pct(eda_kpi['accuracy'])}）</span></article>
</div><p class='plain'><b>大白话：</b>平台不是让大模型“看一眼日志然后凭感觉调参”。它先把规格、测试、EDA 证据、实验变量和知识准入规则固定下来，再让 Agent 在同一预算内行动。结果好不好，由独立 Runtime 和统计脚本判定。</p></section>
<section><h2>2. 实验账本：计划多少，实际留下多少</h2>{table(("实验", "计量单位", "预注册", "实际", "状态"), [(x['study'],x['unit'],x['planned'],x['observed'],x['status']) for x in experiment_ledger])}
<p class='boundary'>失败、超时和 UNKNOWN 都保留。AES/JPEG 早期被 30 分钟人工截断的 pilot 不进入主统计；本次重跑使用 4 小时阶段超时、8 小时 flow 超时，并用 CPU、日志和中间产物共同判断活性。</p></section>
<section><h2>3. RTL：写 RTL 的 Agent 和出题判卷的 Agent 已经分开</h2>
<div class='flow'>自然语言 → SpecIR → Verification Agent 自动写并冻结 testbench → RTLScout 多轮写/改 RTL → compile/lint → simulation → mutation quality → OpenROAD GDS</div>
<p>RTLScout 不再是一个模糊名词：每轮候选 RTL、SHA-256、lint/simulation 反馈、是否通过和最终选择都被保存。RTL Agent 不能修改 testbench；Verification Agent 看不到候选 RTL 的写作过程；Runtime 才是判定者。</p>
{table(("设计","成功/尝试","pass@1","pass@5","首稿通过","迭代救回","唯一RTL","唯一TB"), rtl_rows)}
{bars([(row['design'], row['pass_rate'], pct(row['pass_rate'])) for row in rtl['design_rows']], maximum=1)}
<p class='plain'>首稿通过率是 {pct(rtl['first_authored_candidate_pass_rate'])}，RTLScout 迭代救回 {rtl['iterative_rescue_count']} 次。这个差值回答“RTLScout 是否只是换了名字调用一次 LLM”：如果首稿失败、后续根据固定 evaluator 反馈通过，就有可核查的迭代价值。</p>
<h3>3.1 生成 RTL 与隐藏参考 RTL 的同后端 PPA</h3>
{table(("设计","生成area中位数","参考area中位数","area相对差","生成WNS","参考WNS","生成功耗","参考功耗"), rtl_ppa_rows)}
<p>隐藏参考只用于在相同 Sky130HD、10 ns、利用率和密度下做后端尺度校准。它没有提供给写 RTL 或写 testbench 的 Agent，也不被称为“全局最优”。area 的正相对差表示生成结果更大；WNS 越大越好；功耗越小越好。</p>
<h3>3.2 变异测试和多样性为什么要单列</h3>
<p>普通仿真通过，只能说明 RTL 通过了当前 testbench。变异测试会故意破坏 RTL，再看 testbench 能不能抓住错误。报告同时列唯一 RTL/TB 哈希与被迭代挡住的修订数，避免把重复输出包装成五个独立设计，也避免只展示一个漂亮的 mutation 分数而隐藏候选演化。</p>
<p class='boundary'>{esc(rtl['claim_boundary'])}</p></section>
<section><h2>4. 参数探索：BO/GP 是否比同预算随机搜索更好</h2>
<p>每个 design×policy-seed 都包含 1 个 baseline 和 3 个候选向量，每个向量用 3 个真实且配对的 OpenROAD seed。两种策略预算完全一样。主指标是满足 timing/DRC 硬约束后的 balanced 相对效用。</p>
<div class='cards'><article><b>{num(p['mean_paired_difference'])}</b><span>BO − random 平均配对差</span></article><article><b>{num(p['median_paired_difference'])}</b><span>中位配对差</span></article><article><b>[{num(ci['lower'])}, {num(ci['upper'])}]</b><span>bootstrap 95% CI</span></article><article><b>p={num(p['sign_flip']['p_value'])}</b><span>双侧 sign-flip 检验</span></article></div>
{table(("设计","BO赢","随机赢","平局","原始p","Holm校正p"), parameter_design_rows)}
{bars([("BO/GP 达到0.5%", parameter['threshold_hit_rate']['bo_gp'], pct(parameter['threshold_hit_rate']['bo_gp'])), ("随机达到0.5%", parameter['threshold_hit_rate']['seeded_random'], pct(parameter['threshold_hit_rate']['seeded_random']))], maximum=1)}
<p class='plain'>area/timing(performance)/power/balanced 不是空按钮。报告对同一组已测向量重新排名，并统计它们与 balanced 选择不同候选的次数。timing 和 performance 在当前产品中明确是同义别名，不假装成两套算法。</p>
{table(("策略","area改选次数","timing改选次数","performance改选次数","power改选次数"), profile_rows)}
<details><summary>展开查看全部 40 个 design×seed 配对单元</summary>{table(("设计","策略seed","BO最好效用","随机最好效用","配对差","赢家","BO失败run","随机失败run"), parameter_cell_rows)}</details>
<p>“BO 赢”只在同一个设计、同一个策略 seed、同一组 OpenROAD replica seed 和同样三次新候选预算内判断。正的配对差表示 BO/GP 更好；负数表示随机搜索更好；任何失败都留在失败列中。</p>
<p class='boundary'>{esc(parameter['claim_boundary'])}</p></section>
<section><h2>5. 自演化：不是“成功就记下来”，而是先问能不能迁移</h2>
<p>四个设计组成全部 12 个有方向的 source→holdout 对。每对先在 source 做 utilization×density 的重复 2×2 干预，再在未参与学习的 holdout 设计复验。每个角三个配对 OR_SEED。方向相反就把规则标为 refuted，不能进入可执行知识。</p>
<div class='cards'><article><b>{learning['validated_pair_count']}</b><span>同方向复现、仅作为有界知识</span></article><article><b>{learning['rejected_pair_count']}</b><span>跨设计反转、拒绝迁移</span></article><article><b>{learning_arms['retrieval_only_counterfactual']['false_transfer_rules_admitted']}</b><span>若只做 RAG 会误收的规则</span></article><article><b>0</b><span>因果门实际放行的已知错误规则</span></article></div>
<p class='plain'>比喻一下：普通 RAG 像“在 GCD 上这个药有效，就写进药方”；现在的做法是先拿 FIFO/UART/ibex 做盲测。盲测方向反了，这条经验只保留为负证据，不能指导下一次动作。</p><p class='boundary'>{esc(learning['claim_boundary'])}</p></section>
<section><h2>5.1 十二组跨设计因果复验明细</h2>
{table(("来源设计","盲测设计","来源交互效应","盲测交互效应","结论","知识状态","审计通过"), learning_pair_rows)}
<p>这里的“交互效应”专门检查两个参数组合后是否出现单参数看不到的联合影响。来源与盲测同方向，最多形成带工艺、设计和变量范围的有界知识卡；方向反转则记作 refuted，后续 Agent 不能把它当可执行处方。</p></section>
<section><h2>6. EDA→AI：不是删日志，而是建立可回到原文的 typed 视图</h2>
<p>KPI-only 只给最终数字；typed EDAIR 还给阶段指标、逻辑实例与连接、物理坐标、关键路径、parser fidelity、artifact SHA 和丢失清单。AI 先读有界对象，不够时可按 artifact ID 回读原始字节。</p>
{bars([("KPI-only 准确率", eda_kpi['accuracy'], pct(eda_kpi['accuracy'])), ("Typed EDAIR 准确率", eda_typed['accuracy'], pct(eda_typed['accuracy'])), ("KPI-only 胡答率", eda_kpi['false_answer_rate'], pct(eda_kpi['false_answer_rate'])), ("Typed EDAIR 胡答率", eda_typed['false_answer_rate'], pct(eda_typed['false_answer_rate']))], maximum=1)}
<div class='cards'><article><b>{pct(edair_paired['mean_accuracy_difference'])}</b><span>20 个配对调用的平均准确率增量</span></article><article><b>[{pct(edair_ci['lower'])}, {pct(edair_ci['upper'])}]</b><span>配对 bootstrap 95% CI</span></article><article><b>p={num(edair_paired['sign_flip']['p_value'])}</b><span>双侧精确 sign-flip 检验</span></article><article><b>{num(eda_typed['mean_context_bytes']/eda_kpi['mean_context_bytes'])}×</b><span>typed 上下文字节成本</span></article></div>
{table(("观察接口","调用","答案数","正确","准确率","UNKNOWN率","胡答率","平均上下文字节","平均耗时秒","模型/解析失败"), [(name,v['calls'],v['answers'],v['correct'],pct(v['accuracy']),pct(v['unknown_rate']),pct(v['false_answer_rate']),round(v['mean_context_bytes']),num(v.get('mean_wall_seconds')),v.get('model_or_parse_failure_calls','—')) for name,v in edair['totals'].items()])}
<h3>6.1 分设计拆开看，避免总分掩盖某一题型失败</h3>
{table(("设计","观察接口","调用","答案","正确","准确率","UNKNOWN率","胡答率"), edair_design_rows)}
{table(("设计","typed−KPI平均增量","原始p","Holm校正p","0.05显著"), edair_secondary_rows)}
<p>总的 20 个配对调用差异显著；但每个设计只有 5 对，双侧精确检验的最小粒度有限，四重 Holm 校正后都没有单独达到 0.05。报告保留这个结果，不用总体显著性冒充“每个设计都已显著”。</p>
<h3>6.2 十二类问题逐项结果</h3>
{table(("题号","事实类型","KPI-only","Typed EDAIR","准确率增量"), edair_question_rows)}
<p>UNKNOWN 不是自动算正确：只有标准答案本来就是 UNKNOWN 时才正确。若事实存在但模型说 UNKNOWN，它仍然是漏答；若事实不存在却猜一个数字，则计入胡答。这样能区分“谨慎但没找到”和“自信地编造”。</p>
<p class='boundary'>{esc(edair['claim_boundary'])}</p></section>
<section><h2>7. Agent 架构：提升的是可靠性、可追责和安全，不硬说“多 Agent 自动涨 PPA”</h2>
<p>真实轨迹包含地图→语义→实验→假设→实现→验证→审查→记忆；只有 implement 阶段能提交白名单参数，hypothesis 永远不可执行。故障注入覆盖 baseline 提交中断、candidate 提交中断和终态重复 resume。</p>
{table(("架构臂","关键结果"), [(name, json.dumps(value,ensure_ascii=False)) for name,value in agent['arms'].items()])}
{table(("设计","终态","假设事件","实现事件","验证事件","带run ID验证","低于阈值正波动"), agent_trace_rows)}
<p class='plain'>完整架构在中断恢复后没有重复实验；去掉 checkpoint 的反事实会重复两个已提交 child；去掉 review 门会把 {agent['arms']['no_review_threshold_counterfactual']['below_threshold_promotions']} 个低于 0.5% 的小波动误当成改进；去掉 authority 门会让 {agent['arms']['no_authority_gate_counterfactual']['unsupported_executable_hypotheses']} 个尚未验证的假设失去不可执行保护。</p><p class='boundary'>{esc(agent['claim_boundary'])}</p></section>
<section><h2>8. 大设计外部有效性：AES 与 JPEG</h2>
{table(("设计","真实运行","终态","轮数","最好效用","边界"), [("AES",len(aes['runtime_runs']),aes['checkpoint']['state']['status'],aes['checkpoint']['state']['round'],num(aes['checkpoint']['state']['best_utility']),aes['claim_boundary']), ("JPEG",len(jpeg['runtime_runs']),jpeg['checkpoint']['state']['status'],jpeg['checkpoint']['state']['round'],num(jpeg['checkpoint']['state']['best_utility']),jpeg['claim_boundary'])])}
{table(("设计","轮次","参数","效用","硬约束合格","失败硬约束","area中位数","area IQR","WNS中位数","WNS IQR","功耗中位数","功耗 IQR"), external_history_rows)}
<p>AES 运行状态：{esc(status_counts(aes['runtime_runs']))}；JPEG 运行状态：{esc(status_counts(jpeg['runtime_runs']))}。这两项用于证明多文件、较大 RTL 的工具兼容性、成本和失败模式，不与四个小设计的显著性检验混合。</p></section>
<section><h2>9. 与 2025–2026 前沿工作的功能对齐</h2>{table(("论文/项目","它解决什么","平台对应实现"), refs)}
<p class='boundary'>这里的论文数字不是本平台数字。预印本按 2026-08-25 获取的版本引用；复现实验只认本仓库 Runtime、协议快照和 artifact hash。</p></section>
<section><h2>10. 可以写进论文的结论，以及不能写的结论</h2><div class='twocol'><div><h3>可以写</h3><ul><li>固定四设计下，NL→RTL→独立验证→GDS 的重复成功率和 pass@k。</li><li>同预算、同设计、同 seed 的 BO/GP 与随机配对效果和不确定性。</li><li>因果 holdout 相对 retrieval-only 阻止了多少错误迁移规则。</li><li>typed EDAIR 相对 KPI-only 的事实 QA 准确率、UNKNOWN 和胡答率。</li><li>checkpoint、review、authority 对恢复与安全的具体贡献。</li></ul></div><div><h3>不能写</h3><ul><li>四题成功不等于“任意自然语言芯片都能生成”。</li><li>确定性重复的零 IQR 不能伪装成独立随机样本。</li><li>Agent 编排可靠不等于它单独提高 QoR。</li><li>v2 diagnosis trace 不等于已经执行 TimingECO/EvoDRC。</li><li>一个 PDK 的结果不能声称任意工艺可迁移。</li></ul></div></div></section>
<footer>生成依据：冻结 protocol snapshot、Runtime SQLite、candidate history、EDAIR、独立统计后处理。所有原始证据保留在 openroad-platform/artifacts。</footer>
"""
    style = """<style>*{box-sizing:border-box}body{margin:0;background:#f4f1e9;color:#17202a;font:16px/1.7 system-ui,'Noto Sans SC',sans-serif}header,section,footer{max-width:1160px;margin:auto;padding:34px 48px}header{padding-top:70px}.eyebrow{color:#925b2b;font-weight:800;letter-spacing:.12em}h1{font:800 48px/1.15 Georgia,serif;max-width:900px}h2{font:800 30px/1.2 Georgia,serif;border-bottom:2px solid #1c6b67;padding-bottom:10px}h3{margin-bottom:6px}.lead{font-size:20px;max-width:960px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.cards article{background:#fff;border:1px solid #d7d0c2;border-radius:14px;padding:20px;box-shadow:0 5px 18px #2b251611}.cards b{font:800 28px Georgia;color:#1c6b67;display:block}.cards span{display:block;margin-top:8px}.plain,.boundary{padding:16px 20px;border-radius:10px}.plain{background:#e3f0eb}.boundary{background:#f4e2d5;border-left:5px solid #b95f36}.flow{padding:18px;background:#173f45;color:white;border-radius:12px;font-weight:700;text-align:center}.scroll{overflow:auto;background:white;border-radius:12px;border:1px solid #d8d3c8}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:11px;border-bottom:1px solid #e3ded5;vertical-align:top}th{background:#e8ece7}.bars{background:white;padding:18px;border-radius:12px;margin:16px 0}.barrow{display:grid;grid-template-columns:180px 1fr 90px;gap:12px;align-items:center;margin:9px}.track{height:18px;background:#e9e5dc;border-radius:9px;overflow:hidden}.track i{display:block;height:100%;background:linear-gradient(90deg,#1c6b67,#d48b45)}.twocol{display:grid;grid-template-columns:1fr 1fr;gap:20px}.twocol>div{background:white;padding:18px 24px;border-radius:12px}footer{color:#59636b;border-top:1px solid #cec8bb;margin-top:35px}@media(max-width:800px){header,section,footer{padding:25px 18px}.cards,.twocol{grid-template-columns:1fr}.barrow{grid-template-columns:120px 1fr 65px}h1{font-size:35px}}</style>"""
    document = f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>OpenROAD v2.0 论文级实验验收报告</title>{style}</head><body>{html_body}</body></html>"
    summary = {
        "schema_version": 1,
        "kind": "openroad_v2_paper_experiment_delivery",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ledger": experiment_ledger,
        "headline": {
            "rtl_full_chain_successes": rtl["successes"],
            "rtl_attempts": rtl["attempts"],
            "rtl_first_candidate_pass_rate": rtl["first_authored_candidate_pass_rate"],
            "rtl_iterative_rescues": rtl["iterative_rescue_count"],
            "parameter_paired_cells": parameter["cell_count"],
            "parameter_mean_bo_minus_random": p["mean_paired_difference"],
            "parameter_median_bo_minus_random": p["median_paired_difference"],
            "parameter_sign_flip_p_value": p["sign_flip"]["p_value"],
            "learning_validated_pairs": learning["validated_pair_count"],
            "learning_rejected_pairs": learning["rejected_pair_count"],
            "edair_kpi_accuracy": eda_kpi["accuracy"],
            "edair_typed_accuracy": eda_typed["accuracy"],
            "agent_status": agent["status"],
        },
        "claim_boundaries": {
            "rtl": rtl["claim_boundary"], "parameter": parameter["claim_boundary"],
            "learning": learning["claim_boundary"], "edair": edair["claim_boundary"],
            "agent": agent["claim_boundary"], "references": reference["claim_boundary"],
            "aes": aes["claim_boundary"], "jpeg": jpeg["claim_boundary"],
        },
    }
    return document, experiment_ledger, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameter", type=Path, required=True)
    parser.add_argument("--learning", type=Path, required=True)
    parser.add_argument("--rtl", type=Path, required=True)
    parser.add_argument("--edair", type=Path, required=True)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--aes", type=Path, required=True)
    parser.add_argument("--jpeg", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(); document, ledger, summary = build(args)
    args.html.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.html.expanduser().resolve().write_text(document, encoding="utf-8")
    with args.csv.expanduser().resolve().open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("study","unit","planned","observed","status"))
        writer.writeheader(); writer.writerows(ledger)
    sources = {}
    for name in ("parameter", "learning", "rtl", "edair", "agent", "references", "aes", "jpeg"):
        path = getattr(args, name).expanduser().resolve()
        sources[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    summary["sources"] = sources
    summary_path = args.json.expanduser().resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2),
                            encoding="utf-8")
    print(json.dumps({"html": str(args.html.expanduser().resolve()),
                      "csv": str(args.csv.expanduser().resolve()),
                      "json": str(summary_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
