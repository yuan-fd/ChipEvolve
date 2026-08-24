"""Pre-registered, failure-inclusive experiment summaries for v2 papers.

The harness never labels a result "significant".  It creates an immutable
protocol, reports all supplied runs, and uses a deterministic bootstrap
interval so an attractive single run cannot become a platform claim.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from statistics import median
from typing import Any, Iterable, Mapping


def preregister_protocol(*, study_id: str, question: str, designs: Iterable[str],
                         arms: Mapping[str, Mapping[str, Any]], metrics: Mapping[str, str],
                         repetitions: int, budget: Mapping[str, int], stopping_rule: str) -> dict[str, Any]:
    if not study_id.strip() or not question.strip() or repetitions < 2 or not stopping_rule.strip():
        raise ValueError("invalid protocol fields")
    design_list = list(designs)
    if not design_list or len(set(design_list)) != len(design_list) or len(arms) < 2 or not metrics:
        raise ValueError("protocol needs unique designs, at least two arms and metrics")
    if any(direction not in {"min", "max"} for direction in metrics.values()):
        raise ValueError("metric direction must be min or max")
    if any(not isinstance(value, int) or value < 1 for value in budget.values()):
        raise ValueError("budget must contain positive integer limits")
    payload = {"schema_version": 1, "kind": "paper_protocol", "study_id": study_id, "question": question,
               "designs": design_list, "arms": {key: dict(value) for key, value in arms.items()},
               "metrics": dict(metrics), "repetitions": repetitions, "budget": dict(budget),
               "stopping_rule": stopping_rule,
               "requirements": ["record every terminal run", "use pinned toolchain and artifact hashes",
                                "report failures and variation", "hold out at least one design for transfer claims"]}
    payload["protocol_sha256"] = _digest(payload)
    return payload


def summarize_arm(protocol: Mapping[str, Any], *, arm: str, design: str,
                  metric: str, values: Iterable[float], terminal_statuses: Iterable[str],
                  bootstrap_samples: int = 2000) -> dict[str, Any]:
    if protocol.get("kind") != "paper_protocol" or arm not in protocol.get("arms", {}):
        raise ValueError("unknown protocol arm")
    if design not in protocol.get("designs", []) or metric not in protocol.get("metrics", {}):
        raise ValueError("design or metric is outside protocol")
    points = [float(x) for x in values]; statuses = list(terminal_statuses)
    if len(points) != len(statuses):
        raise ValueError("each repetition needs one terminal status and one metric slot")
    successes = [value for value, status in zip(points, statuses) if status == "succeeded"]
    seed = int(_digest({"protocol": protocol["protocol_sha256"], "arm": arm, "design": design, "metric": metric})[:16], 16)
    interval = _bootstrap_median(successes, seed=seed, samples=bootstrap_samples) if len(successes) >= 2 else None
    return {"protocol_sha256": protocol["protocol_sha256"], "arm": arm, "design": design, "metric": metric,
            "direction": protocol["metrics"][metric], "terminal_statuses": statuses,
            "run_count": len(statuses), "success_count": len(successes),
            "failure_rate": (len(statuses) - len(successes)) / len(statuses) if statuses else 1.0,
            "median": median(successes) if successes else None,
            "minimum": min(successes) if successes else None, "maximum": max(successes) if successes else None,
            "bootstrap_median_95ci": interval,
            "claim": "descriptive, pre-registered and failure-inclusive; not a significance claim"}


def compare_arms(baseline: Mapping[str, Any], candidate: Mapping[str, Any], *,
                 minimum_relative_improvement: float) -> dict[str, Any]:
    for key in ("protocol_sha256", "design", "metric", "direction"):
        if baseline.get(key) != candidate.get(key):
            raise ValueError(f"arms differ in {key}")
    if baseline.get("median") is None or candidate.get("median") is None:
        return {"eligible": False, "reason": "one arm has no successful repetitions"}
    direction, base, value = baseline["direction"], float(baseline["median"]), float(candidate["median"])
    relative = (base - value) / max(abs(base), 1e-12) if direction == "min" else (value - base) / max(abs(base), 1e-12)
    failure_safe = candidate["failure_rate"] <= baseline["failure_rate"]
    return {"eligible": relative >= minimum_relative_improvement and failure_safe,
            "relative_median_improvement": relative, "failure_safe": failure_safe,
            "required_threshold": minimum_relative_improvement,
            "baseline_ci": baseline.get("bootstrap_median_95ci"), "candidate_ci": candidate.get("bootstrap_median_95ci"),
            "claim": "eligible only for the pre-registered practical-improvement claim"}


class PaperProtocolStore:
    """Immutable protocol receipts; results must cite the stored protocol hash."""
    def __init__(self, path: str):
        self.path = path
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS paper_protocols_v1 (protocol_sha256 TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")

    def add(self, protocol: Mapping[str, Any]) -> str:
        if protocol.get("kind") != "paper_protocol" or not isinstance(protocol.get("protocol_sha256"), str):
            raise ValueError("invalid paper protocol")
        encoded = json.dumps(dict(protocol), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if _digest({key: value for key, value in protocol.items() if key != "protocol_sha256"}) != protocol["protocol_sha256"]:
            raise ValueError("paper protocol digest mismatch")
        with self._connect() as connection:
            try: connection.execute("INSERT INTO paper_protocols_v1 VALUES (?, ?)", (protocol["protocol_sha256"], encoded))
            except sqlite3.IntegrityError:
                row = connection.execute("SELECT payload_json FROM paper_protocols_v1 WHERE protocol_sha256=?", (protocol["protocol_sha256"],)).fetchone()
                if row is None or row[0] != encoded: raise ValueError("protocol receipt collision")
        return protocol["protocol_sha256"]

    def get(self, digest: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM paper_protocols_v1 WHERE protocol_sha256=?", (digest,)).fetchone()
        if row is None: raise KeyError(digest)
        return json.loads(row[0])

    def _connect(self): return sqlite3.connect(self.path)


def _bootstrap_median(values: list[float], *, seed: int, samples: int) -> list[float]:
    if not 100 <= samples <= 20000: raise ValueError("bootstrap sample bound is invalid")
    rng = random.Random(seed); n = len(values)
    estimates = sorted(median([values[rng.randrange(n)] for _ in range(n)]) for _ in range(samples))
    return [estimates[int(.025 * (samples - 1))], estimates[int(.975 * (samples - 1))]]


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
