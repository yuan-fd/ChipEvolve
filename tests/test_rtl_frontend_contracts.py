from __future__ import annotations

import pytest

from openroad_platform_contracts import PortSpec, RTLCandidate, SpecIR, VerificationPackage


def spec() -> SpecIR:
    return SpecIR(
        spec_id="spec-gcd-v1", design_id="gcd", top="gcd",
        functionality="Compute the greatest common divisor after start.",
        objective="Pass the frozen functional oracle before PPA evaluation.",
        ports=(PortSpec("clk", "input"), PortSpec("start", "input"),
               PortSpec("done", "output")),
        clock="clk", acceptance_criteria=("done is asserted only after a completed operation",),
    )


def test_spec_ir_has_stable_fingerprint_and_cannot_hide_rtl():
    value = spec()
    restored = SpecIR.from_dict(value.to_dict())
    assert restored.fingerprint == value.fingerprint
    payload = value.to_dict()
    payload["rtl_source"] = "module gcd; endmodule"
    with pytest.raises(ValueError, match="Unknown SpecIR fields"):
        SpecIR.from_dict(payload)


def test_verification_package_is_independent_from_candidate_source():
    verification = VerificationPackage(
        verification_id="verify-gcd-v1", spec_id="spec-gcd-v1",
        compile_checks=("verilator-lint", "yosys-read-verilog"),
        simulation_oracle_refs=("artifact:tb-gcd-sha256",),
        formal_property_refs=("source:gcd-properties-v1",),
        coverage_targets={"functional": 0.9},
    )
    candidate = RTLCandidate(
        candidate_id="rtl-gcd-1", spec_id="spec-gcd-v1", verification_id="verify-gcd-v1",
        rtl_artifact_ref="artifact:rtl-gcd-1-sha256", generator="rtlscout-v2",
    )
    assert verification.to_dict()["spec_id"] == candidate.to_dict()["spec_id"]
    with pytest.raises(ValueError, match="durable RTL artifact"):
        RTLCandidate(
            candidate_id="rtl-gcd-2", spec_id="spec-gcd-v1", verification_id="verify-gcd-v1",
            rtl_artifact_ref="module gcd; endmodule", generator="rtlscout-v2",
        ).validate()
