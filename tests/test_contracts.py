from __future__ import annotations

import pytest

from openroad_platform_contracts import RunRequest, RunStage


def test_run_request_round_trip(tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input a, output y); assign y = a; endmodule\n")
    request = RunRequest(
        rtl_path=str(rtl), top="top", target_stage=RunStage.ROUTE,
        labels={"project": "smoke"},
    )
    request.validate()
    restored = RunRequest.from_dict(request.to_dict())
    assert restored == request
    assert restored.target_stage is RunStage.ROUTE


@pytest.mark.parametrize("field,value", [
    ("core_utilization_pct", 100),
    ("place_density", 0),
    ("clock_period_ns", -1),
    ("stage_timeout_seconds", 0),
])
def test_run_request_rejects_invalid_numeric_fields(tmp_path, field, value):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule\n")
    values = {"rtl_path": str(rtl), field: value}
    with pytest.raises(ValueError):
        RunRequest(**values).validate()

