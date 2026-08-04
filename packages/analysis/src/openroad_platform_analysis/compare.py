#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis/compare.py — 策略对比工具。

把两次 RTL-to-GDS 运行的 report.json 并排比较，自动总结差异。

用法：
    python3 -m analysis.compare --ref  report_a.json --cand report_b.json
    python3 -m analysis.compare --ref  demo_output/gds/run1/analysis/report.json \\
                                 --cand demo_output/gds/run2/analysis/report.json

输出 JSON 含：
    - verdict: 哪次更好（"ref" / "cand" / "tie"）
    - diff: 关键指标差异
    - summary: 一句话结论

加 --markdown 输出表格格式（贴到群里看）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 关注的关键指标（用于对比的「仪表盘」）
COMPARE_KEYS = [
    ("instance_count", "标准单元数", ""),
    ("instance_area_um2", "实例面积", "µm²"),
    ("area_um2", "总面积", "µm²"),
    ("utilization_pct", "核心利用率", "%"),
    ("setup_wns_ns", "setup WNS", "ns"),
    ("setup_tns_ns", "setup TNS", "ns"),
    ("hold_wns_ns", "hold WNS", "ns"),
    ("drc_errors", "DRC 违例", "个"),
    ("wirelength_um", "总线长", "µm"),
    ("skew_ns", "时钟偏移 skew", "ns"),
    ("fmax_mhz", "最大频率", "MHz"),
    ("power_W", "总功耗", "W"),
    ("via_count", "via 数", ""),
]

# 哪个方向算"变好"
BETTER_DIRECTION = {
    "setup_wns_ns": "higher",    # WNS 越大越好
    "setup_tns_ns": "higher",
    "hold_wns_ns": "higher",
    "drc_errors": "lower",
    "utilization_pct": "mid",    # 50-70% 最好
    "fmax_mhz": "higher",
    "power_W": "lower",
    "wirelength_um": "lower",
    "skew_ns": "lower",
    "via_count": "lower",
}


def _load(path: str | Path) -> dict:
    p = Path(path)
    if p.is_dir():
        candidates = list(p.rglob("analysis/report.json"))
        if not candidates:
            raise FileNotFoundError(f"{p} 下未找到 analysis/report.json")
        p = candidates[0]
    return json.loads(p.read_text(encoding="utf-8"))


def _kpi(report: dict, key: str):
    return (report.get("kpi") or {}).get(key)


def _verdict(report: dict):
    return (report.get("diagnosis") or {}).get("verdict", "unknown")

def _errors(report: dict):
    diag = report.get("diagnosis") or {}
    return sum(1 for v in diag.get("violations", []) if v["severity"] == "error")


def _warnings(report: dict):
    diag = report.get("diagnosis") or {}
    return sum(1 for v in diag.get("violations", []) if v["severity"] == "warning")


def multi_compare(reports: list[dict], names: list[str] | None = None) -> dict:
    """多策略对比：任意多个 report.json 并排比较。"""
    if names is None:
        names = [r.get("design", f"run{i+1}") for i, r in enumerate(reports)]
    assert len(reports) == len(names)

    diff = []
    for key, zh, unit in COMPARE_KEYS:
        vals = []
        for r in reports:
            v = _kpi(r, key)
            vals.append(_fmt(v, unit) if v is not None else "—")
        if all(v == "—" for v in vals):
            continue
        diff.append({"metric": key, "name": zh, "values": vals})

    err_counts = [_errors(r) for r in reports]
    warn_counts = [_warnings(r) for r in reports]
    verdicts = [_verdict(r) for r in reports]
    min_err = min(err_counts)
    best_idx = err_counts.index(min_err)

    return {
        "names": names,
        "verdicts": verdicts,
        "errors": err_counts,
        "warnings": warn_counts,
        "best": names[best_idx],
        "best_verdict": verdicts[best_idx],
        "diff": diff,
    }


def multi_compare_markdown(result: dict) -> str:
    """多策略对比结果 → Markdown 表格。"""
    lines = ["## 多策略对比结果\n"]
    lines.append(f"**最优**：{result['best']}（{result['best_verdict']}）\n")
    lines.append(f"| 策略 | " + " | ".join(result['names']) + " |")
    lines.append("|------|" + "|".join("---" for _ in result['names']) + "|")
    lines.append(f"| 裁定 | " + " | ".join(result['verdicts']) + " |")
    lines.append(f"| 错误数 | " + " | ".join(str(e) for e in result['errors']) + " |")
    lines.append(f"| 警告数 | " + " | ".join(str(w) for w in result['warnings']) + " |")
    for d in result["diff"]:
        lines.append(f"| {d['name']} | " + " | ".join(d['values']) + " |")
    return "\n".join(lines)


def compare(ref: dict, cand: dict) -> dict:
    """对比两份 report.json，返回结构化的比较结果。"""
    ref_name = ref.get("design", "ref")
    cand_name = cand.get("design", "cand")

    diff = []
    ref_score = 0
    cand_score = 0

    for key, zh, unit in COMPARE_KEYS:
        rv = _kpi(ref, key)
        cv = _kpi(cand, key)
        if rv is None and cv is None:
            continue

        direction = BETTER_DIRECTION.get(key)
        better = None  # "ref" | "cand" | "same" | None

        if rv is not None and cv is not None and direction:
            if direction == "higher":
                if cv > rv: better = "cand"
                elif rv > cv: better = "ref"
                else: better = "same"
            elif direction == "lower":
                if cv < rv: better = "cand"
                elif rv < cv: better = "ref"
                else: better = "same"
            elif direction == "mid":
                pass  # 不纳入计分

        if better == "cand":
            cand_score += 1
        elif better == "ref":
            ref_score += 1

        rv_fmt = _fmt(rv, unit)
        cv_fmt = _fmt(cv, unit)

        diff.append({
            "metric": key,
            "name": zh,
            "ref": rv_fmt,
            "cand": cv_fmt,
            "better": better,
        })

    # 综合 verdict
    ref_v = _verdict(ref)
    cand_v = _verdict(cand)
    ref_err = _errors(ref)
    cand_err = _errors(cand)

    if cand_err < ref_err:
        overall = "cand"
        summary = f"{cand_name} 更优：错误从 {ref_err} 降到 {cand_err}"
    elif ref_err < cand_err:
        overall = "ref"
        summary = f"{ref_name} 更优：错误从 {cand_err} 降到 {ref_err}"
    elif ref_v == "clean" and cand_v != "clean":
        overall = "ref"
        summary = f"{ref_name} 更优：cand 有违例"
    elif cand_v == "clean" and ref_v != "clean":
        overall = "cand"
        summary = f"{cand_name} 更优：ref 有违例"
    else:
        if cand_score > ref_score:
            overall = "cand"
            summary = f"{cand_name} 在 {cand_score - ref_score} 个指标上更优"
        elif ref_score > cand_score:
            overall = "ref"
            summary = f"{ref_name} 在 {ref_score - cand_score} 个指标上更优"
        else:
            overall = "tie"
            summary = "两次运行质量接近，按目标取舍。"

    return {
        "ref_name": ref_name,
        "cand_name": cand_name,
        "verdict": overall,
        "summary": summary,
        "ref_verdict": ref_v,
        "cand_verdict": cand_v,
        "ref_errors": ref_err,
        "cand_errors": cand_err,
        "diff": diff,
        "ref_wins": ref_score,
        "cand_wins": cand_score,
    }


def _fmt(v, unit=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) < 0.001:
            return f"{v:.2e}{unit}"
        return f"{v:.4g}{unit}"
    return f"{v}{unit}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="RTL-to-GDS 策略对比")
    ap.add_argument("--ref", required=True, help="基准运行（report.json 或工作目录）")
    ap.add_argument("--cand", required=True, help="候选运行（report.json 或工作目录）")
    ap.add_argument("--markdown", action="store_true", help="输出 Markdown 表格")
    a = ap.parse_args(argv)

    ref = _load(a.ref)
    cand = _load(a.cand)
    result = compare(ref, cand)

    if a.markdown:
        print(f"# 策略对比：{result['ref_name']} vs {result['cand_name']}")
        print()
        print(f"**结论**：{result['summary']}")
        print()
        print(f"| 指标 | {result['ref_name']} | {result['cand_name']} | 优劣 |")
        print("|------|------|------|------|")
        for d in result["diff"]:
            mark = {"ref": "←", "cand": "→", "same": "="}.get(d["better"], "")
            print(f"| {d['name']} | {d['ref']} | {d['cand']} | {mark} |")
        print()
        print(f"错误: ref={result['ref_errors']}, cand={result['cand_errors']}")
        print(f"裁定: {result['verdict']}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
