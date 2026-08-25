import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_v2_causal_holdout.py"
SPEC = importlib.util.spec_from_file_location("causal_auditor", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


def test_validated_card_can_be_retrieval_eligible_but_not_directly_executable():
    checks = MODULE.knowledge_safety_checks({
        "validation": {"outcome": "validated"},
        "knowledge_card": {"status": "validated", "action_eligible": True,
                           "execution_allowed": False},
    })
    assert all(checks.values())


def test_refuted_card_must_not_be_action_eligible():
    checks = MODULE.knowledge_safety_checks({
        "validation": {"outcome": "rejected"},
        "knowledge_card": {"status": "refuted", "action_eligible": True,
                           "execution_allowed": False},
    })
    assert checks["knowledge_status_matches_holdout"] is True
    assert checks["refuted_rule_not_action_eligible"] is False
    assert checks["knowledge_card_not_directly_executable"] is True
