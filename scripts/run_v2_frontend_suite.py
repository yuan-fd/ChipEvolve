#!/usr/bin/env python3
"""Run the fixed v2 RTL verification suite with reproducible evidence.

This is a baseline/fixture harness, not a claim that an LLM generated the
golden RTL.  It proves that each package's frozen Testbench can be compiled,
simulated, and linted repeatedly before an RTLScout arm is compared against it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "benchmarks" / "v2"
DEFAULT_TOOLS = Path("/share/home/yuanwenjie/.local/opt/openroad-rtl-tools/bin")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], *, cwd: Path, timeout: int = 120) -> tuple[int, str, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True,
                                timeout=timeout, check=False)
        return result.returncode, (result.stdout + result.stderr)[-20_000:], time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc), time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--tools", type=Path, default=DEFAULT_TOOLS)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be between 1 and 20")
    tools = args.tools.expanduser().resolve()
    iverilog = shutil.which("iverilog") or str(tools / "iverilog")
    vvp = shutil.which("vvp") or str(tools / "vvp")
    verilator = shutil.which("verilator") or str(tools / "verilator")
    for tool in (iverilog, vvp, verilator):
        if not Path(tool).is_file() and shutil.which(tool) is None:
            raise SystemExit(f"required RTL tool is unavailable: {tool}")
    records = []
    for package in sorted(path for path in SUITE.iterdir() if path.is_dir()):
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        top = manifest["top"]
        for repeat in range(args.repeats):
            work = args.output.expanduser().resolve() / package.name / f"repeat-{repeat:02d}"
            work.mkdir(parents=True, exist_ok=True)
            binary = work / "sim.out"
            compile_code, compile_log, compile_seconds = run(
                [iverilog, "-g2012", "-s", "tb", "-o", str(binary),
                 str(package / manifest["testbench"]), str(package / manifest["golden_rtl"])],
                cwd=ROOT,
            )
            sim_code, sim_log, sim_seconds = (run([vvp, str(binary)], cwd=ROOT)
                                              if compile_code == 0 else (None, "", 0.0))
            lint_code, lint_log, lint_seconds = run(
                [verilator, "--lint-only", "--language", "1800-2012", "--top-module", top,
                 str(package / manifest["golden_rtl"])], cwd=ROOT,
            )
            records.append({
                "design": package.name, "repeat": repeat, "top": top,
                "golden_sha256": sha(package / manifest["golden_rtl"]),
                "testbench_sha256": sha(package / manifest["testbench"]),
                "compile": {"status": "passed" if compile_code == 0 else "failed", "seconds": compile_seconds,
                            "log": compile_log},
                "simulation": {"status": "passed" if sim_code == 0 and re.search(r"\bPASS\b", sim_log) else "failed",
                                "exit_code": sim_code, "seconds": sim_seconds, "log": sim_log},
                "lint": {"status": "passed" if lint_code == 0 else "failed", "seconds": lint_seconds,
                         "log": lint_log},
            })
    summary = {"schema_version": 1, "kind": "v2_frontend_suite_report",
               "suite_root": str(SUITE), "repeats": args.repeats,
               "records": records,
               "all_compile_pass": all(x["compile"]["status"] == "passed" for x in records),
               "all_simulation_pass": all(x["simulation"]["status"] == "passed" for x in records),
               "all_lint_pass": all(x["lint"]["status"] == "passed" for x in records),
               "source": "platform-authored golden RTL and frozen testbenches; not LLM evidence"}
    args.output.expanduser().resolve().mkdir(parents=True, exist_ok=True)
    (args.output.expanduser().resolve() / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("repeats", "all_compile_pass", "all_simulation_pass", "all_lint_pass")}, ensure_ascii=False))
    return 0 if all(summary[key] for key in ("all_compile_pass", "all_simulation_pass", "all_lint_pass")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
