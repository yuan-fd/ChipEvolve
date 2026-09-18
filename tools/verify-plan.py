#!/usr/bin/env python3
"""Submit one execution plan and print its recorded evidence."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "lost"}


def request(method: str, url: str, payload: dict[str, Any] | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(call, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def evidence(base_url: str, plan: dict[str, Any]) -> dict[str, Any]:
    runs = []
    for step in plan["steps"]:
        run_id = step.get("run_id")
        if not run_id:
            continue
        segment = urllib.parse.quote(run_id, safe="")
        root = f"{base_url}/kernel/runs/{segment}"
        runs.append(
            {
                "step_id": step["step_id"],
                "run": request("GET", root)["run"],
                "artifacts": request("GET", root + "/artifacts")["artifacts"],
                "environment": request(
                    "GET",
                    root + "/artifacts?category=environment&format=toolchain-snapshot",
                )["artifacts"],
                "metrics": request("GET", root + "/metrics?complete_only=1")["metrics"],
                "resources": request("GET", root + "/resources")["resources"],
                "logs": request("GET", root + "/logs?offset=0&max_bytes=8192")["logs"],
            }
        )
    return {"plan": plan, "runs": runs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8700")
    parser.add_argument("--timeout", type=float, default=7200)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    payload = json.loads(args.plan.read_text(encoding="utf-8"))
    created = request("POST", f"{base_url}/app/plan_executor/plans", payload)["plan"]
    plan_id = urllib.parse.quote(created["plan_id"], safe="")
    plan_url = f"{base_url}/app/plan_executor/plans/{plan_id}"
    deadline = time.monotonic() + args.timeout
    plan = created
    while plan["status"] not in TERMINAL and time.monotonic() < deadline:
        time.sleep(1)
        plan = request("GET", plan_url)["plan"]

    print(json.dumps(evidence(base_url, plan), indent=2, ensure_ascii=False))
    if plan["status"] not in TERMINAL:
        print(
            f"plan {created['plan_id']} did not finish before timeout", file=sys.stderr
        )
        return 2
    return 0 if plan.get("execution_valid") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
