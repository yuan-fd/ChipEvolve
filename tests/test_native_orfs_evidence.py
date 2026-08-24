import hashlib
import json

import pytest

from openroad_platform_analysis import native_orfs_run_view


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path):
    rtl = tmp_path / "dut.sv"; rtl.write_text("module dut; endmodule\n")
    root = tmp_path / "run"; (root / "analysis").mkdir(parents=True)
    plan = {"run_id": "native-1", "design": "dut", "workdir": str(root),
            "request": {"rtl_path": str(rtl), "top": "dut", "clock": None,
                        "platform": "sky130hd", "target_stage": "finish",
                        "clock_period_ns": 10.0, "core_utilization_pct": 30.0,
                        "place_density": .45, "minimum_die_size_um": None,
                        "stage_timeout_seconds": 1800}, "tools": {"orfs_commit": "abc"}}
    (root / "plan.json").write_text(json.dumps(plan))
    report = {"design": "dut", "platform": "sky130hd", "flow_status": "completed",
              "kpi": {"area_um2": 10.0, "setup_wns_ns": .1, "power_W": .02}}
    (root / "analysis" / "report.json").write_text(json.dumps(report))
    artifacts = []
    for path in (root / "plan.json", root / "analysis" / "report.json"):
        artifacts.append({"path": str(path.relative_to(root)), "size_bytes": path.stat().st_size,
                          "sha256": _digest(path)})
    result = {"run_id": "native-1", "status": "succeeded", "design": "dut", "workdir": str(root),
              "artifacts": artifacts, "metrics": [
                  {"name": "finish__design__instance__area", "value": 10.0},
                  {"name": "finish__timing__setup__ws", "value": .1},
                  {"name": "finish__power__total", "value": .02},
              ]}
    (root / "run_result.json").write_text(json.dumps(result))
    return root


def test_native_orfs_evidence_requires_hashed_plan_and_report(tmp_path):
    root = _fixture(tmp_path)
    view = native_orfs_run_view(root)
    assert view["evidence_origin"] == "native-orfs-cli-artifacts"
    assert view["run"]["task_spec"]["parameters"]["core_utilization_pct"] == 30.0
    assert view["run"]["task_spec"]["inputs"]["rtl"]["sha256"] == _digest(tmp_path / "dut.sv")


def test_native_orfs_evidence_rejects_metric_tampering(tmp_path):
    root = _fixture(tmp_path)
    result = json.loads((root / "run_result.json").read_text())
    result["metrics"][0]["value"] = 11.0
    (root / "run_result.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="conflicts"):
        native_orfs_run_view(root)


def test_native_orfs_failure_can_be_preserved_for_fail_closed_learning(tmp_path):
    root = _fixture(tmp_path)
    result = json.loads((root / "run_result.json").read_text())
    result["status"] = "failed"
    (root / "run_result.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="not a completed"):
        native_orfs_run_view(root)
    view = native_orfs_run_view(root, allow_non_success=True)
    assert view["run"]["status"] == "failed"
