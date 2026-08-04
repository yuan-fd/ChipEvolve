#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis/diagnosis.py — 规则诊断引擎（"质检工程师"）

输入：analysis.parsers.stage_json 的输出（+ 可选的 cell_coords 密度结果）
输出：违例清单 + 严重度 + 证据 + 可执行建议

设计原则：
  · 全部基于确定性规则，不让 LLM 判断数字。LLM 只负责在 reporter 里做翻译。
  · 每条规则都带 evidence（证据），任何结论都能追溯到具体指标。
  · 阈值集中在模块顶部，方便按工艺/设计规模调整。

CLI:
    python3 -m analysis.diagnosis --input metrics.json
    python3 -m analysis.parsers.stage_json --workdir ... | python3 -m analysis.diagnosis --pipe
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict

# ── 阈值（按需调整）─────────────────────────────────────────────────
TH = {
    "util_high_pct": 75.0,      # 利用率上限
    "util_low_pct": 20.0,       # 利用率下限（小设计常低于此值，仅提示）
    "util_low_min_insts": 10,   # 低于这个单元数就不提利用率过低
    "skew_ns": 0.10,            # 时钟偏移上限
    "fanout_max": 40,           # 最大扇出
    "density_hotspot_pct": 0.85,  # 网格密度热点阈值
    "hotspot_count_warn": 3,    # 热点数量告警线
    "slack_tight_ratio": 0.05,  # 裕量小于周期的 5% 视为紧张
    "big_design_insts": 5000,
}

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Violation:
    type: str
    severity: str            # error | warning | info
    stage: str
    message: str
    recommendation: str
    evidence: dict = field(default_factory=dict)


@dataclass
class DiagnosisResult:
    design: str | None
    has_errors: bool
    has_warnings: bool
    violations: list
    observations: list
    summary: str
    verdict: str             # clean | acceptable | needs_improvement | failed

    def to_dict(self) -> dict:
        d = asdict(self)
        d["violations"] = [v if isinstance(v, dict) else asdict(v) for v in self.violations]
        return d


def _m(metrics: dict, stage: str, key: str, default=None):
    return (metrics.get("stages", {}).get(stage, {}).get("metrics", {}) or {}).get(key, default)


def _first(metrics: dict, key: str, stages=("finish", "route", "cts", "place")):
    """同一个指标可能出现在多个阶段，按签核优先级取。"""
    for s in stages:
        v = _m(metrics, s, key)
        if v is not None:
            return v, s
    return None, None


# ──────────────────────────────────────────────────────────────────────
def diagnose(stage_metrics: dict, density: dict | None = None) -> dict:
    """入口：传入 stage_json 输出（可选 cell_coords 输出），返回诊断结果。"""
    vs: list[Violation] = []
    period = stage_metrics.get("clock_period_ns")
    summary = stage_metrics.get("summary", {}) or {}

    # ── 0. 流程完整性（最先判，没跑完就别谈时序）
    done = summary.get("stages_completed", 0)
    total = summary.get("stages_total", 6)
    if done < total:
        order = list((stage_metrics.get("stages") or {}).keys())
        expected_stage = summary.get("expected_stage", "finish")
        expected = order[:order.index(expected_stage) + 1] if expected_stage in order else order
        missing = [s for s in expected
                   if stage_metrics["stages"][s].get("status") != "completed"]
        # 从日志找具体错误
        log_detail = (summary.get("flow_log") or "")
        detail_msg = ""
        if log_detail:
            detail_msg = f"\n    {log_detail[:200]}"
        vs.append(Violation(
            "flow_incomplete", "error", "flow",
            f"流程只完成了 {done}/{total} 个阶段，未完成：{', '.join(missing) or '未知'}。{detail_msg}",
            "查看日志中的具体错误。常见原因：PDN 面积太小、工艺库缺失、RTL 不可综合。",
            {"stages_completed": done, "stages_total": total, "missing": missing}))

    # ── 1. setup 时序违例
    wns, st = _first(stage_metrics, "setup_wns_ns")
    tns, _ = _first(stage_metrics, "setup_tns_ns")
    if wns is not None:
        if wns < 0:
            need = round(period - wns, 2) if period else None
            vs.append(Violation(
                "setup_timing_violation", "error", st,
                f"WNS = {wns} ns，存在 setup 时序违例"
                + (f"（TNS = {tns} ns）。" if tns is not None else "。"),
                "三条路可选：① 把时钟周期放宽到 "
                + (f"{need} ns 以上重跑；" if need else "更大值重跑；")
                + "② 回到 RTL 插流水线寄存器，缩短关键路径；"
                + "③ 降低目标利用率（CORE_UTILIZATION）给布局布线的时序优化留出空间。",
                {"setup_wns_ns": wns, "setup_tns_ns": tns,
                 "clock_period_ns": period, "suggested_period_ns": need}))
        elif period and wns < period * TH["slack_tight_ratio"]:
            vs.append(Violation(
                "setup_timing_tight", "warning", st,
                f"setup 裕量仅 {wns} ns（不到周期的 {TH['slack_tight_ratio']*100:.0f}%），时序很紧。",
                "当前能收敛，但没有余量。若后续还要改动 RTL 或提高频率，先预留裕量。",
                {"setup_wns_ns": wns, "clock_period_ns": period}))
        else:
            fmax = round(1000.0 / (period - wns), 1) if period and period - wns > 0 else None
            vs.append(Violation(
                "timing_clean", "info", st,
                f"时序收敛，setup 裕量 {wns} ns。",
                (f"裕量还有富余：把时钟周期压到约 {round(period - wns, 2)} ns 再跑一次，"
                 f"理论上可做到 {fmax} MHz。" if fmax else "时序无违例。"),
                {"setup_wns_ns": wns, "fmax_mhz": fmax}))

    # ── 2. hold 时序违例
    hold, hst = _first(stage_metrics, "hold_wns_ns")
    if hold is not None and hold < 0:
        vs.append(Violation(
            "hold_timing_violation", "error", hst,
            f"hold WNS = {hold} ns，存在保持时间违例。",
            "hold 违例通常靠插 delay buffer 修复。检查 ORFS 是否启用了 hold fixing；"
            "若 CTS 后 skew 偏大，先解决时钟树问题再修 hold。",
            {"hold_wns_ns": hold}))

    # ── 3. 利用率
    util, ust = _first(stage_metrics, "utilization_pct", ("finish", "place", "floorplan"))
    insts, _ = _first(stage_metrics, "instance_count", ("finish", "route", "place", "synth"))
    if util is not None:
        if util > TH["util_high_pct"]:
            vs.append(Violation(
                "high_utilization", "warning", ust,
                f"核心利用率 {round(util,1)}%，偏高。单元摆得太密，布线易拥塞、时序更难收敛。",
                f"把 config.mk 里的 CORE_UTILIZATION 调到 60–70%（当前 {round(util,1)}%）重跑，"
                "通常能同时缓解拥塞和时序。",
                {"utilization_pct": round(util, 2), "threshold": TH["util_high_pct"]}))
        elif util < TH["util_low_pct"] and (insts or 0) > TH["util_low_min_insts"]:
            vs.append(Violation(
                "low_utilization", "info", ust,
                f"核心利用率仅 {round(util,1)}%，芯片面积用得比较浪费。",
                "小电路上这很常见，不影响功能。若在意面积，可调高 CORE_UTILIZATION 或缩小 core。",
                {"utilization_pct": round(util, 2)}))

    # ── 4. DRC
    drc, dst = _first(stage_metrics, "drc_errors", ("finish", "route"))
    if drc:
        vs.append(Violation(
            "drc_violation", "error", dst,
            f"存在 {int(drc)} 个 DRC 违例，布线未完全收敛，这份版图不能视为干净结果。",
            "先降低利用率给布线让路；若仍不收敛，检查是否有超密的局部区域（见密度热点），"
            "或放宽时钟周期减少布线器的时序压力。",
            {"drc_errors": int(drc)}))

    # ── 5. 天线违例
    ant = _m(stage_metrics, "route", "antenna_violations")
    if ant:
        vs.append(Violation(
            "antenna_violation", "warning", "route",
            f"存在 {int(ant)} 个天线违例网络。",
            "开启 ORFS 的天线修复（antenna diode 插入），或让布线器做 layer hopping。",
            {"antenna_violations": int(ant)}))

    # ── 6. 时钟偏移
    skew = _m(stage_metrics, "cts", "skew_ns")
    if skew is not None:
        if skew > TH["skew_ns"]:
            vs.append(Violation(
                "high_clock_skew", "warning", "cts",
                f"时钟偏移 {skew} ns，超过阈值 {TH['skew_ns']} ns，会吃掉时序裕量。",
                "检查时钟树 buffer 层级与负载是否均衡；必要时降低利用率，给时钟树 buffer 腾位置。",
                {"skew_ns": skew, "threshold": TH["skew_ns"]}))
        else:
            vs.append(Violation(
                "clock_tree_ok", "info", "cts",
                f"时钟树质量良好，skew = {skew} ns。",
                "无需处理。",
                {"skew_ns": skew}))

    # ── 7. 密度热点（需要 cell_coords 数据）
    if density and density.get("available"):
        hot = density.get("hotspot_count", 0)
        if hot >= TH["hotspot_count_warn"]:
            sev = "warning" if density.get("method") == "area" else "info"
            vs.append(Violation(
                "density_hotspot", sev, "place",
                f"版图上有 {hot} 个高密度网格（最高 {density.get('max_density')}），存在局部拥塞风险。"
                + ("（注：这是按单元数估算的近似密度）" if density.get("method") != "area" else ""),
                "局部过密说明布局不均衡。可降低整体利用率，或让布局器启用 density balance；"
                "结合热力图看热点是否落在关键路径附近。",
                {"hotspot_count": hot, "max_density": density.get("max_density"),
                 "avg_density": density.get("avg_density"), "method": density.get("method")}))

    # ── 8. 设计规模提示
    if insts and insts > TH["big_design_insts"]:
        vs.append(Violation(
            "large_design", "info", "synth",
            f"设计规模较大（{int(insts)} 个单元），物理设计会比较慢。",
            "演示时建议先用小电路跑通流程，再跑大设计。",
            {"instance_count": int(insts)}))

    # ── 汇总 ─────────────────────────────────────────────────────────
    vs.sort(key=lambda v: SEVERITY_ORDER.get(v.severity, 9))
    findings = [v for v in vs if v.severity in {"error", "warning"}]
    observations = [v for v in vs if v.severity == "info"]
    n_err = sum(1 for v in findings if v.severity == "error")
    n_warn = sum(1 for v in findings if v.severity == "warning")

    if any(v.type == "flow_incomplete" for v in vs):
        verdict, summary_txt = "failed", "流程未跑完，无法给出完整评估。"
    elif n_err:
        verdict = "needs_improvement"
        summary_txt = f"存在 {n_err} 个错误" + (f"和 {n_warn} 个警告" if n_warn else "") + "，需要修复后重跑。"
    elif n_warn:
        verdict = "acceptable"
        summary_txt = f"功能可用，但有 {n_warn} 个警告值得优化。"
    else:
        verdict = "clean"
        summary_txt = "设计质量良好：时序收敛、无 DRC 违例。"

    return DiagnosisResult(
        design=stage_metrics.get("design"),
        has_errors=n_err > 0, has_warnings=n_warn > 0,
        violations=[asdict(v) for v in findings],
        observations=[asdict(v) for v in observations],
        summary=summary_txt, verdict=verdict,
    ).to_dict()


def main(argv=None):
    ap = argparse.ArgumentParser(description="规则诊断引擎")
    ap.add_argument("--input", default=None, help="stage_json 的输出文件；不给则读 stdin")
    ap.add_argument("--pipe", action="store_true", help="从 stdin 读（与不给 --input 等价）")
    ap.add_argument("--density", default=None, help="cell_coords 的输出（可选）")
    ap.add_argument("--merge", action="store_true",
                    help="把诊断结果合并进原 metrics 一起输出（便于串管道）")
    a = ap.parse_args(argv)

    raw = Path(a.input).read_text() if a.input else sys.stdin.read()
    metrics = json.loads(raw)
    density = json.loads(Path(a.density).read_text()) if a.density else None

    result = diagnose(metrics, density)
    out = {**metrics, "diagnosis": result} if a.merge else result
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
