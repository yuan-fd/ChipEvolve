from __future__ import annotations

from scripts.build_v2_acceptance_manifest import EVIDENCE, build


def test_master_v2_ledger_hashes_every_claim_and_preserves_boundaries():
    ledger = build()
    assert ledger["status"] == "passed"
    assert {row["capability"] for row in ledger["evidence_records"]} == set(EVIDENCE)
    assert all(len(row["evidence_sha256"]) == 64 for row in ledger["evidence_records"])
    assert all(row["claim_boundary"] for row in ledger["evidence_records"])
    assert ledger["product_contract"]["baseline_role"].endswith("not a user mode")
    assert ledger["product_contract"]["server_model"] == "gpt-5.6-terra"
    assert ledger["knowledge_lock"]["embedding_version"].startswith("disabled-bm25")
    assert any("not arbitrary-spec" in item for item in ledger["global_claim_boundaries"])
