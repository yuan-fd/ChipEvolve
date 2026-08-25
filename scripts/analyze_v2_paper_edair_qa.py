#!/usr/bin/env python3
"""Paired paper statistics for the frozen KPI-only versus typed-EDAIR QA study."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


ROOT = Path(__file__).resolve().parents[1]
QUESTION_LABELS = {
    "q01": "finish setup WNS", "q02": "place-stage setup WNS",
    "q03": "logical instance count", "q04": "physical instance count",
    "q05": "timing path count", "q06": "minimum timing slack",
    "q07": "minimum-slack endpoint", "q08": "raw artifact count",
    "q09": "logical port count", "q10": "maximum net fanout",
    "q11": "maximum-fanout net", "q12": "unparsed timing blocks",
}


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values); position = (len(ordered) - 1) * fraction
    lower = math.floor(position); upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _sign_flip(values: list[float]) -> dict:
    observed = abs(statistics.fmean(values)); n = len(values)
    if n > 20:
        raise ValueError("exact sign-flip is intentionally bounded to 20 paired calls")
    extreme = 0; total = 1 << n
    for mask in range(total):
        statistic = abs(sum(value if mask & (1 << index) else -value
                            for index, value in enumerate(values)) / n)
        extreme += statistic >= observed - 1e-15
    return {"method": "exact_two_sided_sign_flip", "draws": total,
            "statistic": observed, "p_value": extreme / total}


def _bootstrap_mean(values: list[float], *, seed: int, draws: int = 50_000) -> dict:
    rng = random.Random(seed); n = len(values)
    samples = [statistics.fmean(values[rng.randrange(n)] for _ in range(n))
               for _ in range(draws)]
    return {"method": "seeded_nonparametric_paired_bootstrap", "draws": draws,
            "estimate": statistics.fmean(values), "confidence_level": .95,
            "lower": _quantile(samples, .025), "upper": _quantile(samples, .975)}


def _holm(pvalues: dict[str, float]) -> dict[str, dict]:
    ordered = sorted(pvalues, key=pvalues.get); count = len(ordered); running = 0.0
    result = {}
    for rank, name in enumerate(ordered, start=1):
        running = max(running, min(1.0, (count - rank + 1) * pvalues[name]))
        result[name] = {"raw_p_value": pvalues[name],
                        "holm_adjusted_p_value": running,
                        "reject_at_0_05": running <= .05}
    return result


def analyze(report_path: Path, protocol_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    protocol_bytes = protocol_path.read_bytes(); protocol = json.loads(protocol_bytes)
    if report.get("protocol_sha256") != hashlib.sha256(protocol_bytes).hexdigest():
        raise ValueError("EDAIR report does not match the frozen protocol")
    calls = report["calls"]
    expected = len(protocol["designs"]) * len(protocol["arms"]) * protocol["repetitions"]
    if len(calls) != expected:
        raise ValueError(f"expected {expected} calls, found {len(calls)}")
    by_key = {(row["design"], row["arm"], row["repetition"]): row for row in calls}
    paired_rows = []
    for design in protocol["designs"]:
        for repetition in range(1, protocol["repetitions"] + 1):
            kpi = by_key[(design, "kpi_only", repetition)]
            typed = by_key[(design, "typed_edair", repetition)]
            kpi_accuracy = kpi["correct"] / kpi["total"]
            typed_accuracy = typed["correct"] / typed["total"]
            paired_rows.append({
                "design": design, "repetition": repetition,
                "kpi_only_accuracy": kpi_accuracy, "typed_edair_accuracy": typed_accuracy,
                "paired_difference": typed_accuracy - kpi_accuracy,
                "kpi_only_unknown": kpi["unknown"], "typed_edair_unknown": typed["unknown"],
                "kpi_only_false_answers": kpi["false_answers"],
                "typed_edair_false_answers": typed["false_answers"],
            })
    differences = [row["paired_difference"] for row in paired_rows]
    design_means = {design: statistics.fmean(
        row["paired_difference"] for row in paired_rows if row["design"] == design)
        for design in protocol["designs"]}
    per_design = {}
    for design in protocol["designs"]:
        selected = [row["paired_difference"] for row in paired_rows if row["design"] == design]
        test = _sign_flip(selected)
        per_design[design] = {"mean_paired_difference": statistics.fmean(selected), **test}
    adjusted = _holm({name: row["p_value"] for name, row in per_design.items()})
    for name in per_design:
        per_design[name].update(adjusted[name])
    question_rows = []
    for question_id in QUESTION_LABELS:
        by_arm = {}
        for arm in protocol["arms"]:
            judgements = [next(item for item in call["judgements"]
                               if item["id"] == question_id)
                          for call in calls if call["arm"] == arm]
            by_arm[arm] = {
                "observations": len(judgements),
                "correct": sum(item["correct"] for item in judgements),
                "accuracy": sum(item["correct"] for item in judgements) / len(judgements),
                "unknown": sum(item["unknown"] for item in judgements),
            }
        question_rows.append({"question_id": question_id,
                              "label": QUESTION_LABELS[question_id], **by_arm,
                              "accuracy_difference": (by_arm["typed_edair"]["accuracy"]
                                                      - by_arm["kpi_only"]["accuracy"])})
    for arm in protocol["arms"]:
        selected = [row for row in calls if row["arm"] == arm]
        report["totals"][arm]["mean_wall_seconds"] = statistics.fmean(
            float(row.get("wall_seconds") or 0) for row in selected)
        report["totals"][arm]["model_or_parse_failure_calls"] = sum(
            row.get("returncode") != 0 or row.get("parse_error") is not None for row in selected)
    return {**report,
            "kind": "v2_paper_edair_qa_paired_analysis",
            "paired_statistics": {
                "unit": "design×repetition call; twelve questions per paired call",
                "pair_count": len(paired_rows),
                "mean_accuracy_difference": statistics.fmean(differences),
                "median_accuracy_difference": statistics.median(differences),
                "bootstrap_mean_95_ci": _bootstrap_mean(differences, seed=20260825),
                "sign_flip": _sign_flip(differences),
                "per_design_secondary": per_design,
            },
            "design_cluster_sensitivity": {
                "unit": "design-level mean over five model repeats",
                "design_count": len(design_means), "design_mean_differences": design_means,
                "mean_accuracy_difference": statistics.fmean(design_means.values()),
                "bootstrap_mean_95_ci": _bootstrap_mean(
                    list(design_means.values()), seed=20260826),
                "sign_flip": _sign_flip(list(design_means.values())),
                "interpretation": (
                    "This four-design sensitivity analysis does not use repeated model calls "
                    "as independent design generalization evidence."),
            },
            "paired_rows": paired_rows, "question_rows": question_rows,
            "claim_boundary": (report["claim_boundary"] +
                " Repeated calls share four underlying design contexts; the primary inference unit is the paired call, and per-design n=5 tests are secondary."),
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path,
                        default=ROOT / "experiments/v2-paper-20260825/edair-protocol.json")
    args = parser.parse_args()
    result = analyze(args.report.expanduser().resolve(), args.protocol.expanduser().resolve())
    output = args.output.expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output),
                      "pairs": result["paired_statistics"]["pair_count"],
                      "mean_difference": result["paired_statistics"]["mean_accuracy_difference"],
                      "p_value": result["paired_statistics"]["sign_flip"]["p_value"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
