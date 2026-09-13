"""The ORFS adapter against the real toolchain.

Everything else in this directory runs against a stub Makefile.  A stub proves
the platform's half of the contract -- configuration written, stages run in
order, each gated on the product it should have produced, evidence collected with
the right kinds -- and proves nothing at all about the other half.  This module
runs the real flow: a real PDK, a real OpenROAD, a real Yosys, real synthesis.

It is skipped unless the toolchain is named, because it needs all three and
because it copies the flow tree (1.6 GB on the machine this was written for):

    OPENROAD_PLATFORM_ORFS_ROOT=~/OpenROAD-flow-scripts \\
    OPENROAD_PLATFORM_OPENROAD_BIN=~/bin/openroad \\
    OPENROAD_PLATFORM_YOSYS_BIN=~/bin/yosys \\
    python3 -m pytest tests/plugins/test_orfs_real_toolchain.py -q

It runs the whole flow to ``finish`` and then the protected evaluator over the
result, because that is the only way to prove the two halves of the platform are
wired to each other.  What that produced on nangate45 for a small counter, so a
later reader can tell "this got slower" from "this broke": six stages in 71 s of
flow time, a ``6_final.gds``, and an admissible verdict with nine metrics
(``setup_wns_ns`` 7.82 against a 10 ns period, ``power_W`` 1.18e-05,
``drc_errors`` 0).  OpenROAD 26Q1-1961-g63ed2e0fe5, Yosys 0.63, ORFS commit
51ad1231a231ee85234c06db807688d029b85c35.

This module exists because the first real run found a defect no stub could: a
relative ``--result`` path wrote relative paths into ``config.mk``, and ``make``
resolves those against the directory it runs in.  That is now fixed and asserted
in ``test_orfs_adapter_process.py``; the test here is what would have caught it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "plugins" / "orfs" / "adapter.py"

ORFS_ROOT = os.environ.get("OPENROAD_PLATFORM_ORFS_ROOT")
OPENROAD_BIN = os.environ.get("OPENROAD_PLATFORM_OPENROAD_BIN")
YOSYS_BIN = os.environ.get("OPENROAD_PLATFORM_YOSYS_BIN")
PLATFORM = os.environ.get("OPENROAD_PLATFORM_REAL_PLATFORM", "nangate45")

pytestmark = pytest.mark.skipif(
    not (ORFS_ROOT and OPENROAD_BIN and YOSYS_BIN),
    reason=(
        "no real toolchain named; set OPENROAD_PLATFORM_ORFS_ROOT, "
        "OPENROAD_PLATFORM_OPENROAD_BIN and OPENROAD_PLATFORM_YOSYS_BIN to copy "
        "the flow tree and run real synthesis"
    ),
)

RTL = """\
module counter (clk, rst_n, q);
  input clk;
  input rst_n;
  output reg [7:0] q;
  always @(posedge clk) begin
    if (!rst_n) q <= 8'b0;
    else q <= q + 1'b1;
  end
endmodule
"""

#: A real synthesis cannot be hurried; the deadline is about stopping a hang,
#: never about asserting speed.
STAGE_TIMEOUT_SECONDS = 1800


@pytest.fixture(scope="module")
def real_run(tmp_path_factory) -> tuple[dict, Path]:
    workspace = tmp_path_factory.mktemp("real-orfs")
    rtl = workspace / "counter.v"
    rtl.write_text(RTL, encoding="utf-8")

    request_path = workspace / "adapter_request.json"
    result_path = workspace / "adapter_result.json"
    request_path.write_text(json.dumps({
        "schema_version": 1,
        "plugin": {"plugin_id": "orfs", "plugin_version": "1.0.0"},
        "task": {
            "schema_version": 2, "task_id": "real-orfs", "project_id": "p",
            "design_id": "counter", "plugin_id": "orfs",
            "inputs": {
                "rtl_path": str(rtl), "platform": PLATFORM, "design": "counter",
                "clock_period_ns": 10.0, "target_stage": "finish",
                "orfs_root": str(Path(ORFS_ROOT).expanduser()),
                "openroad_bin": str(Path(OPENROAD_BIN).expanduser()),
                "yosys_bin": str(Path(YOSYS_BIN).expanduser()),
                "stage_timeout_seconds": STAGE_TIMEOUT_SECONDS,
            },
            "parameters": {"or_seed": 1},
        },
    }), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(ADAPTER),
         "--request", str(request_path), "--result", str(result_path)],
        cwd=str(workspace), env=dict(os.environ), capture_output=True, text=True,
        timeout=STAGE_TIMEOUT_SECONDS * 2,
    )
    assert result_path.is_file(), (
        f"adapter produced no result\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return json.loads(result_path.read_text(encoding="utf-8")), workspace


def test_the_whole_flow_runs(real_run):
    """Every stage ran, in order, and left a real product.

    The size assertion is what separates "the stage exited zero" from "the stage
    produced something": a signoff layout is hundreds of kilobytes, not a
    placeholder.
    """
    result, workspace = real_run
    assert result["status"] == "succeeded", result.get("failure")
    assert result["exit_code"] == 0

    run_result = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8"))
    assert [stage["stage"] for stage in run_result["stages"]] == [
        "synth", "floorplan", "place", "cts", "route", "finish"]
    assert all(stage["status"] == "succeeded" for stage in run_result["stages"])

    base = workspace / "results" / PLATFORM / "counter" / "base"
    for name in ("6_final.odb", "6_final.def", "6_final.v", "6_final.gds"):
        product = base / name
        assert product.is_file() and product.stat().st_size > 0, name


def test_the_real_run_is_registered_as_evidence(real_run):
    result, workspace = real_run
    kinds = {artifact["path"]: artifact["kind"] for artifact in result["artifacts"]}
    assert f"results/{PLATFORM}/counter/base/1_synth.odb" in kinds
    assert kinds["toolchain_snapshot.json"] == "report"
    assert kinds["logs/flow.log"] == "log"
    # Every declared path is relative to the workspace, as the protocol requires.
    for path in kinds:
        assert not path.startswith("/")


def test_the_real_run_records_a_real_toolchain(real_run):
    """The snapshot must name the tools that actually ran.

    A probe that returns nothing is a *recorded* unknown, so the assertion is
    that these are present -- it is the difference between "we could not ask"
    and "this is the version".
    """
    _, workspace = real_run
    snapshot = json.loads(
        (workspace / "toolchain_snapshot.json").read_text(encoding="utf-8"))
    versions = snapshot["versions"]
    assert versions["openroad"], "OpenROAD did not report a version"
    assert versions["yosys"], "Yosys did not report a version"
    assert re.fullmatch(r"[0-9a-f]{40}", versions["orfs_commit"] or ""), versions
    assert snapshot["toolchain"]["fingerprint"]
    # The generated configuration is hashed into the snapshot, so a result can be
    # attributed to the exact configuration it was produced from.
    assert snapshot["files"]["generated_config"]["sha256"]


def test_the_real_run_claims_only_what_it_did(real_run):
    """A completed layout is an implementation, and still not verified logic."""
    _, workspace = real_run
    milestones = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8"))["milestones"]
    assert milestones["synthesizable"] is True
    assert milestones["implementation_valid"] is True
    assert milestones["gds_complete"] is True
    # The platform never claims functional verification: that is a separate
    # capability with its own evidence.
    assert milestones["functionally_verified"] is False


def test_the_design_identity_is_recorded(real_run):
    """The evaluator hashes this file for the design's identity.

    Without it the evaluator abstains -- which is what happened before the
    adapter learned to write it.
    """
    _, workspace = real_run
    manifest = json.loads(
        (workspace / "design_input_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_order"] == ["counter.v"]
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["sources"][0]["sha256"])


def test_the_protected_evaluator_scores_the_real_run(real_run, tmp_path):
    """The other half of the platform, over a report a real flow produced.

    This is the test that proves the two halves are wired to each other: the
    evaluator finds the attempt where the adapter left it, reads the real
    ``6_report.json``, and returns metrics the platform will store.  Before this
    round it abstained on every run -- it looked in a directory nothing creates.
    """
    _, workspace = real_run
    evaluator = REPO_ROOT / "plugins" / "orfs-evaluator" / "adapter.py"
    request_path = tmp_path / "eval_request.json"
    result_path = tmp_path / "eval_result.json"
    request_path.write_text(json.dumps({
        "schema_version": 1,
        "plugin": {"plugin_id": "orfs-evaluator", "plugin_version": "1.0.0"},
        "task": {
            "schema_version": 2, "task_id": "eval", "project_id": "p",
            "design_id": "counter", "plugin_id": "orfs-evaluator",
            # The kernel declares the workspace it is evaluating.
            "inputs": {"workspace": str(workspace)}, "parameters": {},
        },
    }), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(evaluator),
         "--request", str(request_path), "--result", str(result_path)],
        cwd=str(tmp_path), env=dict(os.environ), capture_output=True, text=True,
        timeout=STAGE_TIMEOUT_SECONDS,
    )
    assert result_path.is_file(), completed.stderr

    verdict = json.loads(
        (workspace / "protected_evaluation.json").read_text(encoding="utf-8"))
    assert verdict["status"] == "admissible", verdict.get("reason")

    metrics = {metric["name"]: metric["value"] for metric in verdict["metrics"]}
    # The clock period is the design's constraint; a positive setup slack below
    # it is the only thing a 10 ns counter can mean.
    assert 0 < metrics["setup_wns_ns"] < 10.0, metrics
    assert metrics["drc_errors"] == 0, metrics
    assert metrics["power_W"] > 0, metrics
    # Every metric cites a file that exists in the workspace.  That traceability
    # is what makes the verdict admissible rather than a claim, and it is checked
    # against the real files rather than against a fixture.
    for metric in verdict["metrics"]:
        store_key = metric["context"]["source_artifact_store_key"]
        cited = workspace / store_key
        assert cited.is_file() and cited.stat().st_size > 0, store_key
    assert (workspace / "common_evaluation.json").is_file()


def test_the_shared_checkout_is_not_written_to(real_run):
    """``make`` runs in the attempt's own copy of the flow.

    This is the property v1's generated-design adapter gave up: it installed
    design directories *inside* the shared checkout (``flow/designs/<pdk>/
    opv2_<top>_<hash>/``), so one experiment changed the toolchain under every
    other one.
    """
    _, workspace = real_run
    staged = workspace / "orfs-flow"
    assert (staged / "Makefile").is_file()
    assert (workspace / "designs" / PLATFORM / "counter" / "config.mk").is_file()
    # Nothing the run generated was written under the shared checkout's flow
    # directory by this run: the workspace's own designs directory holds it.
    assert not (Path(ORFS_ROOT).expanduser() / "flow" / "designs" / PLATFORM
                / "counter").exists()
