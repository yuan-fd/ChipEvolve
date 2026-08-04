#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis/reporter.py — 把指标 + 诊断 + 密度组装成最终报告，并生成 DeepSeek 的 prompt。

分工（重要）：
  · 数字：EDA 工具产生（stage_json 从 ORFS 的 JSON 里读）
  · 问题：规则判定（diagnosis，确定性，不经过 LLM）
  · 人话：LLM 翻译（本模块只生成 prompt 字符串，不调用 LLM）
LLM 永远不许自己编造 WNS、面积、功耗——prompt 里已把所有数字给全，并明确禁止推测。

CLI:
    python3 -m analysis.reporter --metrics m.json --diagnosis d.json [--density c.json]
    python3 -m analysis.parsers.stage_json --workdir X | \
      python3 -m analysis.diagnosis --merge | python3 -m analysis.reporter --pipe
    加 --prompt 只打印给 LLM 的 prompt 文本。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

STAGE_ZH = {
    "synth": "综合", "floorplan": "布局规划", "place": "放置",
    "cts": "时钟树综合", "route": "布线", "finish": "签核与 GDS 输出",
}
VERDICT_ZH = {
    "clean": "✅ 干净（无违例）",
    "acceptable": "⚠ 可接受（有警告）",
    "needs_improvement": "❌ 需要改进（有错误）",
    "failed": "💥 流程未完成",
}
UNITS = {
    "instance_count": ("标准单元数", ""), "instance_area_um2": ("实例面积", " µm²"),
    "die_area_um2": ("die 面积", " µm²"), "core_area_um2": ("core 面积", " µm²"),
    "utilization_pct": ("核心利用率", "%"), "io_count": ("IO 端口数", ""),
    "net_count": ("网络数", ""), "wirelength_um": ("总线长", " µm"),
    "estimated_wirelength_um": ("预估线长", " µm"), "via_count": ("via 数", ""),
    "drc_errors": ("DRC 违例", " 个"), "antenna_violations": ("天线违例", " 个"),
    "skew_ns": ("时钟偏移 skew", " ns"), "insertion_delay_ns": ("时钟插入延迟", " ns"),
    "clock_buffer_count": ("时钟 buffer 数", ""), "setup_slack_ns": ("setup 裕量", " ns"),
    "hold_slack_ns": ("hold 裕量", " ns"), "setup_wns_ns": ("setup WNS", " ns"),
    "setup_tns_ns": ("setup TNS", " ns"), "hold_wns_ns": ("hold WNS", " ns"),
    "hold_tns_ns": ("hold TNS", " ns"), "power_W": ("总功耗", " W"),
    "fmax_mhz": ("最大频率", " MHz"), "warnings": ("警告数", ""), "errors": ("错误数", ""),
    "congestion_overflow": ("拥塞溢出", ""), "macro_count": ("宏单元数", ""),
    "via_count": ("via 总数", ""), "via_singlecut_count": ("单孔 via 数", ""),
    "via_multicut_count": ("多层 via 数", ""),
    "antenna_violations": ("天线违例", "个"), "antenna_diode_count": ("天线二极管数", ""),
    "grt_overflow_iterations": ("拥塞迭代次数", "次"),
    "grt_route_time_s": ("全局布线耗时", "s"), "warning_type_count": ("警告类型数", ""),
}


def _fmt(key, val):
    zh, unit = UNITS.get(key, (key, ""))
    if isinstance(val, float):
        if abs(val) < 0.001 and abs(val) > 0:
            val = f"{val:.2e}"
        else:
            val = round(val, 4)
    return f"{zh}: {val}{unit}"


# ──────────────────────────────────────────────────────────────────────
def build_report(design: str, platform: str, stage_metrics: dict, diagnosis: dict,
                 cell_density: dict | None = None,
                 runtime_seconds: float | None = None) -> dict:
    """组装最终报告 JSON —— web-demo / CLI / LLM 都只读这一份。"""
    stages = stage_metrics.get("stages", {}) or {}
    summ = stage_metrics.get("summary", {}) or {}
    verdict = diagnosis.get("verdict", "failed")

    recs, seen = [], set()
    for v in diagnosis.get("violations", []):
        if v.get("severity") == "info":
            continue
        r = v.get("recommendation")
        if r and r not in seen:
            seen.add(r)
            recs.append(r)

    # 首屏 KPI：从各阶段挑最能说明问题的几个
    def pick(stage, key):
        return (stages.get(stage, {}).get("metrics", {}) or {}).get(key)

    kpi = {
        "instance_count": pick("finish", "instance_count") or pick("synth", "instance_count"),
        "area_um2": pick("finish", "instance_area_um2") or pick("place", "instance_area_um2"),
        "utilization_pct": pick("finish", "utilization_pct") or pick("place", "utilization_pct"),
        "setup_wns_ns": pick("finish", "setup_wns_ns") or pick("route", "setup_wns_ns"),
        "hold_wns_ns": pick("finish", "hold_wns_ns") or pick("route", "hold_wns_ns"),
        "drc_errors": pick("finish", "drc_errors") if pick("finish", "drc_errors") is not None
                      else pick("route", "drc_errors"),
        "wirelength_um": pick("route", "wirelength_um"),
        "skew_ns": pick("cts", "skew_ns"),
        "power_W": pick("finish", "power_W") or pick("route", "power_W"),
        "fmax_mhz": pick("finish", "fmax_mhz") or pick("route", "fmax_mhz")
                    or pick("cts", "fmax_mhz"),
        "clock_period_ns": stage_metrics.get("clock_period_ns"),
        "via_count": pick("route", "via_count"),
        "via_singlecut_count": pick("route", "via_singlecut_count"),
        "via_multicut_count": pick("route", "via_multicut_count"),
        "antenna_violations": pick("route", "antenna_violations"),
        "antenna_diode_count": pick("route", "antenna_diode_count"),
        "congestion_overflow": pick("route", "congestion_overflow"),
        "grt_overflow_iterations": pick("route", "grt_overflow_iterations"),
    }

    n_err = sum(1 for v in diagnosis.get("violations", []) if v["severity"] == "error")
    n_warn = sum(1 for v in diagnosis.get("violations", []) if v["severity"] == "warning")
    summary_html = (
        f"<b>{design}</b>（{platform}）：{VERDICT_ZH.get(verdict, verdict)}。"
        f"完成 {summ.get('stages_completed', 0)}/{summ.get('stages_total', 6)} 个阶段，"
        f"{n_err} 个错误、{n_warn} 个警告。{diagnosis.get('summary', '')}"
    )

    return {
        "design": design,
        "platform": platform,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "flow_status": "completed" if summ.get("stages_completed") == summ.get("stages_total")
                       else "incomplete",
        "runtime_seconds": runtime_seconds,
        "verdict": verdict,
        "kpi": kpi,
        "stages": stages,
        "diagnosis": diagnosis,
        "cell_density": cell_density or {"available": False},
        "recommendations": recs,
        "summary_html": summary_html,
        "disclaimer": "本报告基于 OpenROAD 开源流程的分析结果，可用于设计探索与问题定位；"
                      "功耗、寄生参数与 DRC 覆盖度取决于开源工艺数据，不等同于商业工具的 tapeout 签核。",
    }


# ──────────────────────────────────────────────────────────────────────
def build_llm_prompt(report: dict) -> str:
    """生成给 DeepSeek 的 prompt（只拼字符串，不调 LLM）。"""
    lines = ["你是一个 EDA 物理设计分析专家。以下是一次 RTL-to-GDS 流程的完整分析数据。", ""]
    lines += ["## 设计概要",
              f"- 设计名称: {report.get('design')}",
              f"- 工艺平台: {report.get('platform')}",
              f"- 流程状态: {report.get('flow_status')}"]
    if report.get("runtime_seconds"):
        lines.append(f"- 总耗时: {round(report['runtime_seconds'])} 秒")
    if report.get("kpi", {}).get("clock_period_ns"):
        lines.append(f"- 目标时钟周期: {report['kpi']['clock_period_ns']} ns")
    manifest = report.get("run_manifest") or {}
    requested = manifest.get("requested_parameters") or {}
    effective = manifest.get("effective_parameters") or {}
    if requested:
        lines.append(f"- 请求参数: {json.dumps(requested, ensure_ascii=False)}")
    if effective:
        lines.append(f"- 实际参数: {json.dumps(effective, ensure_ascii=False)}")
    lines.append("")

    graybox = report.get("graybox") or {}
    evaluation = report.get("evaluation") or graybox.get("evaluation") or {}
    if evaluation:
        lines.append("## 硬门槛评测")
        lines.append(f"- 候选分类: {evaluation.get('classification')}")
        for gate in evaluation.get("gates", []):
            lines.append(f"- [{gate.get('status')}] {gate.get('gate_id')}: {gate.get('message')}")
        lines.append("")

    parameters = graybox.get("parameters") or []
    if parameters:
        lines.append("## 参数溯源与证据")
        for parameter in parameters:
            lines.append(f"- {parameter.get('orfs_name')}={parameter.get('value')} "
                         f"(confidence={parameter.get('confidence')})")
            for ref in parameter.get("evidence", []):
                if ref.get("line"):
                    lines.append(f"  来源: {ref.get('path')}:{ref.get('line')} | {ref.get('excerpt')}")
        lines.append("")

    graybox_stages = graybox.get("stages") or {}
    if graybox_stages:
        lines.append("## 全流程真实子阶段轨迹")
        for stage in ("synth", "floorplan", "place", "cts", "route", "finish"):
            for substage in (graybox_stages.get(stage) or {}).get("substages", []):
                command = substage.get("command")
                command_note = (f"运行日志确认: {command}" if command else
                                "仅源码确认: " + ", ".join(substage.get("declared_commands") or []))
                lines.append(f"- {stage}/{substage.get('substage_id')}: {substage.get('status')}; "
                             f"gate={substage.get('gate_status')}; {command_note}; "
                             f"metrics={json.dumps(substage.get('metrics', {}), ensure_ascii=False)}")
        lines.append("")

    log_events = (report.get("log_events") or {}).get("events", [])
    important_events = [event for event in log_events if event.get("severity") in ("error", "warning")]
    if important_events:
        lines.append("## 结构化日志事件")
        for event in important_events[:30]:
            lines.append(f"- [{event.get('severity')}] {event.get('category')} | "
                         f"{event.get('source_file')}:{event.get('source_line')} | {event.get('message')}")
        lines.append("")

    deltas = (report.get("stage_deltas") or {}).get("transitions", [])
    if deltas:
        lines.append("## 阶段差分（仅列有共同口径的指标）")
        for transition in deltas:
            for key, change in transition.get("metrics", {}).items():
                lines.append(f"- {transition['from']} → {transition['to']} / {key}: "
                             f"{change['before']} → {change['after']} (Δ {change['delta']})")
        lines.append("")

    lines.append("## 各阶段指标")
    for st, data in (report.get("stages") or {}).items():
        if data.get("status") != "completed" or not data.get("metrics"):
            continue
        lines.append(f"\n### {STAGE_ZH.get(st, st)} ({st})")
        for k, v in data["metrics"].items():
            lines.append(f"- {_fmt(k, v)}")
    lines.append("")

    dens = report.get("cell_density") or {}
    if dens.get("available"):
        lines += ["## 版图密度",
                  f"- 单元总数: {dens.get('total_cells')}",
                  f"- 平均密度: {dens.get('avg_density')}",
                  f"- 最大密度: {dens.get('max_density')}",
                  f"- 高密度热点数: {dens.get('hotspot_count')}",
                  f"- 统计方式: {dens.get('density_unit')}", ""]

    lines.append("## 规则诊断结果（由确定性规则得出，请勿推翻）")
    vios = report.get("diagnosis", {}).get("violations", [])
    if not vios:
        lines.append("- 未发现异常。")
    for v in vios:
        lines.append(f"- [{v['severity'].upper()}] {v['type']} @ {v['stage']}: {v['message']}")
    lines.append("")

    lines += [
        "## 请生成分析报告",
        "根据以上数据，写一段面向数字电路工程师的中文分析报告。",
        "",
        "要求：",
        "1. 严格分为：观察、因果假设、支持证据、不确定性、下一实验；",
        "2. 关键数字直接写数值，不要加粗、不要颜色标记、不要 emoji；",
        "3. 若存在问题，给出具体的可执行改进动作；",
        "4. 语言客观、克制，像资深工程师在写技术评审意见；",
        "5. 每个因果判断必须引用至少两条阶段证据，否则只能写成假设；",
        "6. 下一实验必须只改变一个变量，并说明什么结果会支持或否定假设；",
        "7. 对关键结论使用“来源：文件:行号”引用已有证据，不得编造引用；",
        "8. 明确写出结论可信度，并说明相互矛盾或缺失的证据；",
        "9. 下一实验写出假设、仅一个变化参数、控制变量、成功/失败判定和回滚值；",
        "10. 整体控制在 800 字以内。",
        "",
        "严格约束：只能使用上面提供的数字，不得推测、补全或发明任何未给出的指标。"
        "数据缺失时直接写「该指标未提供」。",
    ]

    # ── 附：历史经验（经验池不可用就跳过，绝不影响报告本身）──────────
    try:
        from openroad_platform_analysis.experience_pool import ExperiencePool

        pool = ExperiencePool()
        stats = pool.stats()
        if stats["total"] > 0:
            lines.append("")
            lines.append("## 历史参考（仅供背景，不得据此推测本次设计的指标）")
            lines.append(f"平台已累计 {stats['total']} 次电路生成记录，"
                         f"成功率 {stats['success_rate']}%，平均重试 {stats['avg_attempts']} 次。")
            similar = pool.search(report.get("design", "") or "", top_k=3)
            if similar:
                names = [f"{s['prompt'][:20]}（{s.get('gates_count', 0)} 门）"
                         for s in similar if s.get("prompt")]
                if names:
                    lines.append("相似设计：" + "、".join(names))
    except Exception:
        pass

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="组装最终分析报告 / 生成 LLM prompt")
    ap.add_argument("--metrics", default=None, help="stage_json 输出")
    ap.add_argument("--diagnosis", default=None, help="diagnosis 输出")
    ap.add_argument("--density", default=None, help="cell_coords 输出（可选）")
    ap.add_argument("--pipe", action="store_true",
                    help="从 stdin 读 diagnosis --merge 的合并结果")
    ap.add_argument("--runtime", type=float, default=None)
    ap.add_argument("--prompt", action="store_true", help="只输出给 LLM 的 prompt 文本")
    ap.add_argument("-o", "--output", default=None)
    a = ap.parse_args(argv)

    if a.pipe or not a.metrics:
        merged = json.loads(sys.stdin.read())
        metrics = {k: v for k, v in merged.items() if k != "diagnosis"}
        diag = merged.get("diagnosis") or {}
    else:
        metrics = json.loads(Path(a.metrics).read_text())
        diag = json.loads(Path(a.diagnosis).read_text()) if a.diagnosis else {}

    density = json.loads(Path(a.density).read_text()) if a.density else metrics.get("cell_density")

    rep = build_report(metrics.get("design"), metrics.get("platform"),
                       metrics, diag, density, a.runtime)

    text = build_llm_prompt(rep) if a.prompt else json.dumps(rep, indent=2, ensure_ascii=False)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"已写入 {a.output}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
