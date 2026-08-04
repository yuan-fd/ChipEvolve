from __future__ import annotations

import pytest

from openroad_platform_analysis import EvidenceContext, EvidenceKnowledgeBase, KnowledgeRecord
from openroad_platform_contracts import RepairAction


SHA = "a" * 64


def context(design="adder", toolchain="orfs-51ad", pdk="nangate45-v1"):
    return EvidenceContext(design, "nangate45", pdk, toolchain)


def record(*, design="adder", toolchain="orfs-51ad", pdk="nangate45-v1",
           scope="exact_design", verified=True, action=None, claim=None):
    return KnowledgeRecord(
        claim=claim or "Lower core utilization reduced routing congestion",
        evidence_ref="artifact:route-report", evidence_sha256=SHA,
        context=context(design, toolchain, pdk), verified=verified,
        scope=scope, tags=("routing", "congestion"), proposed_action=action,
    )


def test_unverified_or_unhashed_claim_cannot_enter_index(tmp_path):
    kb = EvidenceKnowledgeBase(tmp_path / "knowledge.db")
    with pytest.raises(ValueError, match="verified"):
        kb.add(record(verified=False))
    bad = record()
    object.__setattr__(bad, "evidence_sha256", "unknown")
    with pytest.raises(ValueError, match="SHA-256"):
        kb.add(bad)


def test_search_hard_filters_toolchain_pdk_and_design(tmp_path):
    kb = EvidenceKnowledgeBase(tmp_path / "knowledge.db")
    exact = record()
    general = record(design="aes", scope="platform_general",
                     claim="Routing congestion responds to lower utilization")
    wrong_design = record(design="aes")
    wrong_tool = record(toolchain="orfs-other")
    wrong_pdk = record(pdk="nangate45-v2")
    for item in (exact, general, wrong_design, wrong_tool, wrong_pdk):
        kb.add(item)

    results = kb.search("routing congestion utilization", context())
    assert {item["record_id"] for item in results} == {exact.record_id, general.record_id}
    assert all(item["evidence"] == {"ref": "artifact:route-report", "sha256": SHA}
               for item in results)
    assert kb.search("routing congestion", context(toolchain="missing")) == []


def test_replay_revalidates_fingerprint_context_and_action_without_execution(tmp_path):
    action = RepairAction(
        action_id="repair-kb", action_type="lower_core_utilization",
        reason_code="congestion", parameters={"core_utilization_pct": 35.0},
        evidence_refs=("artifact:route-report",),
    )
    kb = EvidenceKnowledgeBase(tmp_path / "knowledge.db")
    item = record(action=action)
    kb.add(item)
    result = kb.search("routing congestion", context())[0]
    replay = kb.replay(result, context())
    assert replay["status"] == "approved_for_policy_evaluation"
    assert replay["executed"] is False
    assert replay["action"]["action_type"] == "lower_core_utilization"
    assert "command" not in replay["action"]
    with pytest.raises(ValueError, match="version mismatch"):
        kb.replay(result, context(toolchain="orfs-other"))
    result["fingerprint"] = "tampered"
    with pytest.raises(ValueError, match="fingerprint"):
        kb.replay(result, context())
