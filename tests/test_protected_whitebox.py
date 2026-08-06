from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from openroad_platform_contracts import MechanismEvidence
from openroad_platform_execution import (
    PatchProposal,
    ProtectedWhiteBoxEvaluator,
    ProtectedWhiteBoxPromotionGate,
    VerificationPolicy,
    WhiteBoxPolicy,
)


TARGET = "tools/OpenROAD/src/dpl_evolve/src/EvolveTelemetry.h"


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(root), *args], text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               check=False)
    assert completed.returncode == 0, completed.stdout
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "openroad-fixture"
    target = repository / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("static constexpr int kTelemetry = 0;\n")
    verifier = repository / "verify.py"
    verifier.write_text('''import json
from pathlib import Path
text=Path("tools/OpenROAD/src/dpl_evolve/src/EvolveTelemetry.h").read_text()
assert "kTelemetry = 1" in text
out=Path(".p15/evidence.json"); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({
 "level":"target_local","scope":"handoff","mechanism_intent":"trace accepted handoff",
 "source_anchors":["EvolveTelemetry::accepted"],"controls":{"enabled":True},
 "liveness":{"status":"live","counters":{"accepted_handoff":3},"log_signals":["DPL_EVOLVE_ACCEPT"]},
 "baseline_metrics":{"hpwl":100.0,"runtime_seconds":2.0},
 "candidate_metrics":{"hpwl":99.5,"runtime_seconds":2.1},
 "legality":True,"full_flow_validated":True,"review_outcome":"mechanism_evidence",
 "compatibility_observations":["handoff remained legal"]
}))
''')
    _git(repository, "init")
    _git(repository, "config", "user.email", "p15@example.invalid")
    _git(repository, "config", "user.name", "P15 Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return repository, _git(repository, "rev-parse", "HEAD")


def _proposal(base_commit: str) -> PatchProposal:
    patch = f'''diff --git a/{TARGET} b/{TARGET}
--- a/{TARGET}
+++ b/{TARGET}
@@ -1 +1 @@
-static constexpr int kTelemetry = 0;
+static constexpr int kTelemetry = 1;
'''
    return PatchProposal(
        proposal_id="p15-telemetry", base_commit=base_commit, patch_text=patch,
        evidence_refs=("docs/evidence/p15/dplevolve-source-lock.json",),
    )


def _policy(*, liveness=("accepted_handoff",)) -> WhiteBoxPolicy:
    return WhiteBoxPolicy(
        verification=VerificationPolicy(
            allowed_paths=("tools/OpenROAD/src/dpl_evolve/**",),
            commands=((sys.executable, "verify.py"),), timeout_seconds=30,
            require_human_for_source=True,
        ),
        evidence_path=".p15/evidence.json",
        required_liveness_counters=liveness,
    )


def test_protected_whitebox_requires_liveness_full_flow_and_human_gate(tmp_path):
    repository, base = _repository(tmp_path)
    candidate = Path(f"/tmp/openroad-platform-p15-test-{uuid.uuid4().hex}")
    evaluator = ProtectedWhiteBoxEvaluator()
    result = evaluator.evaluate(repository, _proposal(base), candidate, _policy())
    try:
        assert result.status == "passed"
        assert result.evaluation.baseline_unchanged is True
        assert result.mechanism_evidence.liveness["counters"]["accepted_handoff"] == 3
        assert MechanismEvidence.from_dict(
            result.mechanism_evidence.to_dict()
        ) == result.mechanism_evidence
        receipt = ProtectedWhiteBoxPromotionGate.review(result, _policy())
        assert receipt["decision"] == "awaiting_human"
        assert receipt["applied"] is False
        approved = ProtectedWhiteBoxPromotionGate.review(
            result, _policy(), human_approved=True,
        )
        assert approved["decision"] == "approved_for_manual_promotion"
        assert approved["applied"] is False
    finally:
        evaluator.dispose(repository, result)
    assert _git(repository, "status", "--short") == ""


def test_whitebox_rejects_missing_required_liveness_and_protected_paths(tmp_path):
    repository, base = _repository(tmp_path)
    candidate = Path(f"/tmp/openroad-platform-p15-test-{uuid.uuid4().hex}")
    evaluator = ProtectedWhiteBoxEvaluator()
    result = evaluator.evaluate(
        repository, _proposal(base), candidate, _policy(liveness=("never_fired",)),
    )
    try:
        assert result.status == "failed"
        assert result.failure.startswith("mechanism_not_live")
        assert ProtectedWhiteBoxPromotionGate.review(result, _policy())["decision"] == "rejected"
    finally:
        evaluator.dispose(repository, result)

    forbidden = _proposal(base)
    forbidden = PatchProposal(
        proposal_id="bad", base_commit=base,
        patch_text=forbidden.patch_text.replace(TARGET, "baseline/evaluator.py"),
        evidence_refs=forbidden.evidence_refs,
    )
    with pytest.raises(ValueError, match="protected evaluator"):
        evaluator.evaluate(repository, forbidden,
                           Path(f"/tmp/openroad-platform-p15-test-{uuid.uuid4().hex}"),
                           _policy())
