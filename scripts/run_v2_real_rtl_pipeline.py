#!/usr/bin/env python3
"""Run the real v2 natural-language RTL pipeline and preserve failure evidence.

This is an acceptance runner, not a fixture shortcut: the benchmark text enters
the same platform-managed Codex Spec Agent, independent Verification Agent,
RTLScout, Runtime lint/simulation/mutation gates, and ORFS baseline used by the
product.  Golden RTL and the checked-in benchmark testbench are deliberately
not passed to the pipeline; they remain external regression references only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def _write_report(output: Path, report: dict) -> None:
    temporary = output / "report.json.tmp"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output / "report.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--design", choices=("gcd", "fifo", "uart_tx", "ibex_alu"),
                        default="gcd")
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    parser.add_argument("--max-spec-turns", type=int, default=2)
    parser.add_argument("--resume", action="store_true",
                        help="resume the single durable RTL pipeline in an existing output directory")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)

    package = ROOT / "benchmarks" / "v2" / args.design
    natural_language = (package / "spec.md").read_text(encoding="utf-8").strip()
    manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
    report: dict = {
        "schema_version": 1,
        "kind": "v2_real_natural_language_rtl_report",
        "design": args.design,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "natural_language": natural_language,
            "source": str((package / "spec.md").relative_to(ROOT)),
            "golden_rtl_supplied_to_agents": False,
            "benchmark_testbench_supplied_to_agents": False,
        },
        "model_policy": "platform-managed codex-cli:gpt-5.6-terra",
        "claim_boundary": (
            "One real attempt with all failures retained. Passing this run does not by "
            "itself prove arbitrary-spec generalization; the four-design suite and "
            "repeated-seed aggregate are the required claim unit."
        ),
    }
    state: ApiState | None = None
    exit_code = 1
    try:
        state = ApiState(
            output / "platform.db", output / "uploads", args.orfs_root,
            design_root=output / "designs", legacy_root=output / "legacy",
            runtime_db_path=output / "runtime.db",
            optimization_db_path=output / "optimization.db",
            load_taiwei_plugin=False,
        )
        health = state.health()
        report["health"] = {key: health.get(key) for key in (
            "execution_ready", "orfs_ready", "openroad", "yosys",
            "server_spec_model_ready", "server_spec_model",
        )}
        if not health["execution_ready"] or not health["server_spec_model_ready"]:
            raise RuntimeError("real ORFS and the platform-managed Spec model must be ready")

        if args.resume:
            checkpoints = state.pipeline_checkpoints.list(
                pipeline_kind="rtl-to-orfs-v2", limit=10)
            if len(checkpoints) != 1:
                raise RuntimeError(
                    f"resume requires exactly one durable RTL pipeline; found {len(checkpoints)}"
                )
            spec = state.rtl_frontend.get_spec(checkpoints[0]["subject_id"]).to_dict()
            report["resumed_from"] = {
                "pipeline_id": checkpoints[0]["pipeline_id"],
                "revision": checkpoints[0]["revision"],
                "status": checkpoints[0]["state"].get("status"),
            }
        else:
            session = state.create_spec_session({"message": natural_language})
            turns = 0
            while not (session.get("state") or {}).get("ready_for_execution"):
                if turns >= max(0, args.max_spec_turns):
                    raise RuntimeError("Spec Agent did not reach an executable SpecIR within the turn budget")
                questions = (session.get("state") or {}).get("clarification_questions") or []
                followup = (
                    "Resolve remaining ambiguity conservatively from the original specification. "
                    "Use synthesizable synchronous RTL, exact declared ports and widths, active-low "
                    "reset when rst_n is named, and no undocumented protocol behavior. Questions: "
                    + " | ".join(str(item) for item in questions)
                )
                session = state.add_spec_turn(session["session_id"], {"message": followup})
                turns += 1
            report["spec_session"] = session
            frozen = state.materialize_specir(session["session_id"], {"confirmed": True})
            spec = frozen["spec"]
        if spec["top"] != manifest["top"]:
            raise RuntimeError(
                f"Spec Agent top mismatch: expected {manifest['top']!r}, observed {spec['top']!r}"
            )
        report["specir"] = spec
        pipeline = state.run_automated_rtl_pipeline(spec["spec_id"], {})
        report["pipeline"] = pipeline
        report["lineage"] = state.rtl_frontend.lineage(spec["spec_id"])
        report["runtime_runs"] = state.list_runtime_runs(limit=200)["runs"]
        report["agent_traces"] = state.agent_traces.list(limit=200)
        exit_code = 0 if pipeline.get("status") == "baseline_succeeded" else 1
    except Exception as exc:  # preserve the complete failed attempt for audit
        report["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        if state is not None:
            report["runtime_runs"] = state.list_runtime_runs(limit=200)["runs"]
            report["agent_traces"] = state.agent_traces.list(limit=200)
    finally:
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        report["status"] = "passed" if exit_code == 0 else "failed"
        _write_report(output, report)

    print(json.dumps({
        "output": str(output), "design": args.design, "status": report["status"],
        "pipeline_status": (report.get("pipeline") or {}).get("status"),
        "failure": (report.get("failure") or {}).get("message"),
    }, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
