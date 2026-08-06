from __future__ import annotations

import dataclasses

import pytest

from openroad_platform_analysis import (
    EvidenceKnowledgeRecordV2,
    EvidenceRAG,
)
from openroad_platform_contracts import EvidencePointer, LearningContext


def _context(*, design="gcd", fingerprint="a" * 64,
             toolchain="orfs-51ad123", stage="finish"):
    return LearningContext(
        design_id=design, design_fingerprint=fingerprint, platform="nangate45",
        pdk_id="nangate45-public", toolchain_id=toolchain, flow_stage=stage,
        metric_parser_version="orfs-stage-json-1",
    )


def _record(claim, kind="observed_fact", *, context=None, scope="exact_design",
            verified=True, suffix="b"):
    return EvidenceKnowledgeRecordV2(
        claim=claim, knowledge_type=kind, context=context or _context(),
        evidence=EvidencePointer(ref=f"artifact:report-{suffix}", sha256=suffix * 64),
        verified=verified, scope=scope, tags=("OpenROAD", "QoR"),
    )


@pytest.mark.parametrize("query", [
    "power utilization", "utilization DRC", "lower power", "core density",
    "OpenROAD power", "QoR tradeoff", "routing clean", "timing tradeoff",
    "area power", "candidate utilization", "observed power", "DRC zero",
    "core utilization", "physical design", "verified QoR", "power candidate",
    "nangate utilization", "flow power", "design tradeoff", "lower candidate power",
])
def test_twenty_curated_queries_return_cited_exact_context(tmp_path, query):
    rag = EvidenceRAG(tmp_path / "rag.db")
    rag.add(_record(
        "Physical design flow observed lower core utilization candidate, lower area and power, "
        "zero DRC, clean routing, and a timing QoR tradeoff on Nangate OpenROAD",
    ))
    bundle = rag.retrieve(query, _context())
    assert bundle.records
    assert bundle.records[0]["evidence"]["sha256"] == "b" * 64
    assert bundle.records[0]["eligible_for_proposal"] is True
    assert rag.replay(bundle, _context()) == bundle


def test_hard_context_filter_precedes_ranking_and_general_scope_is_explicit(tmp_path):
    rag = EvidenceRAG(tmp_path / "rag.db")
    exact = _record("exact design placement density evidence", suffix="b")
    general = _record("platform general placement density range", scope="platform_general",
                      suffix="c")
    hypothesis = _record("unverified placement density guess", kind="hypothesis",
                         verified=False, suffix="d")
    for record in (exact, general, hypothesis):
        rag.add(record)

    same = rag.retrieve("placement density", _context())
    assert {item["record_id"] for item in same.records} == {
        exact.record_id, general.record_id, hypothesis.record_id,
    }
    other_design = rag.retrieve(
        "placement density", _context(design="aes", fingerprint="e" * 64),
    )
    assert [item["record_id"] for item in other_design.records] == [general.record_id]
    wrong_toolchain = rag.retrieve(
        "placement density", _context(toolchain="orfs-other"),
    )
    assert wrong_toolchain.records == ()
    eligible = rag.retrieve("placement density", _context(), action_eligible_only=True)
    assert hypothesis.record_id not in {item["record_id"] for item in eligible.records}


def test_tampered_bundle_and_wrong_context_are_rejected(tmp_path):
    rag = EvidenceRAG(tmp_path / "rag.db")
    rag.add(_record("verified routing evidence"))
    bundle = rag.retrieve("routing evidence", _context())
    with pytest.raises(ValueError, match="context mismatch"):
        rag.replay(bundle, _context(stage="route"))
    tampered = dataclasses.replace(bundle, records=tuple({**item, "claim": "changed"}
                                                         for item in bundle.records))
    with pytest.raises(ValueError, match="tampered"):
        rag.replay(tampered, _context())
