#!/usr/bin/env python3
"""Evaluate one harmless real-repository candidate without touching the baseline."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src"):
    sys.path.insert(0, str(source))

from openroad_platform_execution import (  # noqa: E402
    IsolatedCodingAgent, PatchProposal, PromotionGate, VerificationPolicy,
)


PATCH = """diff --git a/docs/P10_CANDIDATE_ONLY.md b/docs/P10_CANDIDATE_ONLY.md
new file mode 100644
index 0000000..9d447f1
--- /dev/null
+++ b/docs/P10_CANDIDATE_ONLY.md
@@ -0,0 +1,3 @@
+# Candidate-only validation note
+
+This file exists only inside the detached P10 acceptance worktree.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-commit", default="c923f55")
    args = parser.parse_args()
    output = args.output_root.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    candidate = Path("/tmp") / f"openroad-platform-p10-acceptance-{uuid.uuid4().hex}"
    proposal = PatchProposal(
        proposal_id="p10-docs-candidate", base_commit=args.base_commit,
        patch_text=PATCH, evidence_refs=("docs/evidence/P9_KNOWLEDGE_ACCEPTANCE.json",),
    )
    policy = VerificationPolicy(
        allowed_paths=("docs/**",),
        commands=((sys.executable, "-m", "pytest", "-q"),),
        timeout_seconds=600, require_human_for_source=True,
    )
    agent = IsolatedCodingAgent()
    evaluation = agent.evaluate(ROOT, proposal, candidate, policy)
    receipt = PromotionGate.review(evaluation, policy)
    summary = {"schema_version": 1, "phase": "P10",
               "accepted": evaluation.status == "passed" and evaluation.baseline_unchanged,
               "evaluation": evaluation.to_dict(), "promotion_receipt": receipt,
               "baseline_file_absent": not (ROOT / "docs/P10_CANDIDATE_ONLY.md").exists()}
    agent.dispose(ROOT, evaluation)
    summary["candidate_disposed"] = not candidate.exists()
    (output / "acceptance_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0 if summary["accepted"] and summary["baseline_file_absent"] \
        and summary["candidate_disposed"] and receipt["applied"] is False else 2


if __name__ == "__main__":
    raise SystemExit(main())
