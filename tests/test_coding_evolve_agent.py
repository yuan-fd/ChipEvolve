from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from openroad_platform_analysis import (
    EvidenceContext, EvidenceDrivenEvolveAgent, EvidenceKnowledgeBase, KnowledgeRecord,
)
from openroad_platform_execution import (
    IsolatedCodingAgent, PatchProposal, PromotionGate, VerificationPolicy,
)


def repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/value.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "base"], check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                   text=True).strip()
    return repo, head


def candidate_path() -> Path:
    return Path("/tmp") / f"openroad-platform-p10-test-{uuid.uuid4().hex}"


def patch(value: str) -> str:
    return f"""diff --git a/src/value.py b/src/value.py
index 77bd29b..1111111 100644
--- a/src/value.py
+++ b/src/value.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = {value}
"""


def policy() -> VerificationPolicy:
    return VerificationPolicy(
        allowed_paths=("src/*.py",),
        commands=((sys.executable, "-m", "py_compile", "src/value.py"),),
        timeout_seconds=30,
    )


def test_coding_agent_validates_in_detached_worktree_and_never_changes_baseline(tmp_path):
    repo, head = repository(tmp_path)
    before = (repo / "src/value.py").read_bytes()
    proposal = PatchProposal("patch-safe", head, patch("2"), ("artifact:test",))
    agent = IsolatedCodingAgent()
    evaluation = agent.evaluate(repo, proposal, candidate_path(), policy())
    receipt = PromotionGate.review(evaluation, policy())

    assert evaluation.status == "passed"
    assert evaluation.baseline_unchanged is True
    assert (repo / "src/value.py").read_bytes() == before
    assert Path(evaluation.candidate_worktree, "src/value.py").read_text() == "VALUE = 2\n"
    assert receipt == {"decision": "awaiting_human", "applied": False,
                       "evaluation_id": evaluation.evaluation_id,
                       "patch_sha256": proposal.sha256}
    agent.dispose(repo, evaluation)
    assert not Path(evaluation.candidate_worktree).exists()


def test_failed_candidate_is_rejected_and_unsafe_path_never_creates_worktree(tmp_path):
    repo, head = repository(tmp_path)
    agent = IsolatedCodingAgent()
    failed = agent.evaluate(repo, PatchProposal(
        "patch-bad-python", head, patch("("), ("artifact:test",)
    ), candidate_path(), policy())
    assert failed.status == "failed" and failed.failure == "verification_failed"
    assert PromotionGate.review(failed, policy())["decision"] == "rejected"
    agent.dispose(repo, failed)

    unsafe = patch("2").replace("src/value.py", ".github/workflows/pwn.yml")
    target = candidate_path()
    with pytest.raises(ValueError, match="outside|CI/CD"):
        agent.evaluate(repo, PatchProposal("patch-unsafe", head, unsafe,
                                           ("artifact:test",)), target, policy())
    assert not target.exists()


def test_evolve_agent_requires_version_matched_evidence_and_never_executes(tmp_path):
    kb = EvidenceKnowledgeBase(tmp_path / "knowledge.db")
    context = EvidenceContext("adder", "nangate45", "pdk-v1", "orfs-v1")
    kb.add(KnowledgeRecord(
        claim="Improve routing congestion with a bounded utilization experiment",
        evidence_ref="artifact:route", evidence_sha256="b" * 64,
        context=context, verified=True, tags=("routing", "congestion"),
    ))
    proposal = EvidenceDrivenEvolveAgent(kb).propose("improve routing congestion", context)
    payload = proposal.to_dict()
    assert payload["execution_allowed"] is False
    assert payload["required_gate"] == "coding_agent_isolated_validation"
    assert payload["evidence"][0]["sha256"] == "b" * 64
    with pytest.raises(ValueError, match="No version-compatible"):
        EvidenceDrivenEvolveAgent(kb).propose(
            "improve routing congestion",
            EvidenceContext("adder", "nangate45", "pdk-v1", "orfs-v2"),
        )
