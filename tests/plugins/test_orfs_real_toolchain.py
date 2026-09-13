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

``synth`` rather than ``finish``: one real stage proves the chain, and a full flow
is hours.  What a real run at ``synth`` on nangate45 produced, so a later reader
can tell "this got slower" from "this broke": ``1_synth.odb`` at 416 KB in 7.9 s
of flow time, OpenROAD 26Q1-1961-g63ed2e0fe5, Yosys 0.63, ORFS commit
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
                "clock_period_ns": 10.0, "target_stage": "synth",
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


def test_real_synthesis_produces_a_database(real_run):
    """The flow ran and left a real product, not an empty file.

    An ``odb`` at 416 KB is OpenROAD's synthesized database; the size assertion
    is what separates "the stage exited zero" from "the stage produced something".
    """
    result, workspace = real_run
    assert result["status"] == "succeeded", result.get("failure")
    assert result["exit_code"] == 0

    base = workspace / "results" / PLATFORM / "counter" / "base"
    products = [path for path in (base / "1_synth.odb", base / "1_synth.v")
                if path.is_file() and path.stat().st_size > 0]
    assert products, f"no synthesis product in {base}"


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
    """``synth`` is not an implementation.  The milestones say so."""
    _, workspace = real_run
    milestones = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8"))["milestones"]
    assert milestones["synthesizable"] is True
    assert milestones["functionally_verified"] is False
    assert milestones["implementation_valid"] is False
    assert milestones["gds_complete"] is False


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
