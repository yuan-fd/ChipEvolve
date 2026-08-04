"""Isolated patch verification; this module has no production apply operation."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .process_guardian import ProcessGuardian


DIFF_PATH = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)
FORBIDDEN_PREFIXES = (".github/", ".circleci/", ".gitlab", "ci/")


@dataclass(frozen=True)
class PatchProposal:
    proposal_id: str
    base_commit: str
    patch_text: str
    evidence_refs: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.patch_text.encode()).hexdigest()

    def validate(self) -> tuple[str, ...]:
        if not re.fullmatch(r"[0-9a-f]{7,40}", self.base_commit):
            raise ValueError("Patch base_commit is invalid")
        if not self.patch_text or len(self.patch_text.encode()) > 2 * 1024 * 1024:
            raise ValueError("Patch is empty or exceeds 2 MiB")
        if not self.evidence_refs:
            raise ValueError("Patch proposal requires evidence references")
        if not all(ref.startswith(("artifact:", "run:", "docs/evidence/"))
                   for ref in self.evidence_refs):
            raise ValueError("Patch evidence references are not durable")
        if any(marker in self.patch_text for marker in (
            "GIT binary patch", "new file mode 120000", "deleted file mode",
            "old mode ", "new mode 100755",
        )):
            raise ValueError("Binary, symlink, deletion, and mode-change patches are forbidden")
        pairs = DIFF_PATH.findall(self.patch_text)
        if not pairs or len(pairs) > 50:
            raise ValueError("Patch must modify between 1 and 50 text files")
        paths = []
        for left, right in pairs:
            if left != right or left.startswith(".git/") or ".." in Path(left).parts:
                raise ValueError("Patch contains an unsafe path or rename")
            basename = Path(left).name.lower()
            if left.startswith(FORBIDDEN_PREFIXES) or basename == ".env" \
                    or Path(left).suffix.lower() in {".pem", ".key", ".p12"}:
                raise ValueError("Patch targets credentials or CI/CD policy")
            paths.append(left)
        return tuple(paths)


@dataclass(frozen=True)
class VerificationPolicy:
    allowed_paths: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
    timeout_seconds: int = 600
    require_human_for_source: bool = True

    def validate(self) -> None:
        if not self.allowed_paths or not self.commands or len(self.commands) > 10:
            raise ValueError("Verification policy requires paths and 1-10 commands")
        if not 1 <= self.timeout_seconds <= 600:
            raise ValueError("Verification timeout is outside policy")
        if any(not command or any(not isinstance(part, str) or not part for part in command)
               for command in self.commands):
            raise ValueError("Verification commands must be non-empty argv tuples")


@dataclass(frozen=True)
class CandidateEvaluation:
    evaluation_id: str
    status: str
    proposal_id: str
    base_commit: str
    patch_sha256: str
    modified_files: tuple[str, ...]
    candidate_worktree: str
    checks: tuple[dict, ...]
    baseline_unchanged: bool
    failure: str | None = None

    def to_dict(self) -> dict:
        return {
            "evaluation_id": self.evaluation_id, "status": self.status,
            "proposal_id": self.proposal_id, "base_commit": self.base_commit,
            "patch_sha256": self.patch_sha256, "modified_files": list(self.modified_files),
            "candidate_worktree": self.candidate_worktree, "checks": list(self.checks),
            "baseline_unchanged": self.baseline_unchanged, "failure": self.failure,
        }


class IsolatedCodingAgent:
    def __init__(self, *, guardian: ProcessGuardian | None = None):
        self.guardian = guardian or ProcessGuardian()

    def evaluate(self, repository: str | Path, proposal: PatchProposal,
                 candidate_worktree: str | Path,
                 policy: VerificationPolicy) -> CandidateEvaluation:
        repository = Path(repository).expanduser().resolve()
        candidate = Path(candidate_worktree).expanduser().resolve()
        policy.validate()
        paths = proposal.validate()
        if candidate.exists():
            raise FileExistsError(f"Candidate worktree already exists: {candidate}")
        if not candidate.as_posix().startswith("/tmp/openroad-platform-"):
            raise ValueError("Candidate worktree must use the node-local /tmp prefix")
        if not all(any(fnmatch.fnmatch(path, pattern) for pattern in policy.allowed_paths)
                   for path in paths):
            raise ValueError("Patch modifies a path outside the policy")
        before = self._baseline_snapshot(repository)
        checks = []
        failure = None
        try:
            self._git(repository, "worktree", "add", "--detach", str(candidate),
                      proposal.base_commit)
            check = subprocess.run(["git", "-C", str(candidate), "apply", "--check", "-"],
                                   input=proposal.patch_text, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=30, check=False)
            checks.append({"name": "git_apply_check", "exit_code": check.returncode,
                           "output": check.stdout[-4000:]})
            if check.returncode:
                failure = "patch_check_failed"
            else:
                applied = subprocess.run(["git", "-C", str(candidate), "apply", "-"],
                                         input=proposal.patch_text, text=True,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         timeout=30, check=False)
                checks.append({"name": "git_apply", "exit_code": applied.returncode,
                               "output": applied.stdout[-4000:]})
                if applied.returncode:
                    failure = "patch_apply_failed"
            if failure is None:
                # Make newly-created files visible to `git diff --name-only`
                # without staging their content or creating a commit.
                self._git(candidate, "add", "-N", "--", *paths)
                changed = tuple(filter(None, self._git(
                    candidate, "diff", "--name-only", "--no-ext-diff"
                ).splitlines()))
                if set(changed) != set(paths):
                    failure = "changed_path_mismatch"
                else:
                    for index, command in enumerate(policy.commands, 1):
                        log = candidate / f".candidate-check-{index}.log"
                        outcome = self.guardian.run(
                            list(command), cwd=candidate,
                            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                 "HOME": os.environ.get("HOME", "/tmp"),
                                 "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                            log_path=log, timeout_seconds=policy.timeout_seconds,
                        )
                        checks.append({"name": f"verification_{index}",
                                       "argv": list(command),
                                       "exit_code": outcome.returncode,
                                       "timed_out": outcome.timed_out,
                                       "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                                       "output": log.read_text(encoding="utf-8",
                                                               errors="replace")[-4000:]})
                        if outcome.returncode or outcome.timed_out:
                            failure = "verification_failed"
                            break
        except Exception as exc:
            failure = f"evaluator_error:{type(exc).__name__}:{exc}"
        after = self._baseline_snapshot(repository)
        unchanged = before == after
        if not unchanged:
            failure = "baseline_changed"
        return CandidateEvaluation(
            evaluation_id=f"evaluation-{uuid.uuid4().hex}",
            status="passed" if failure is None else "failed",
            proposal_id=proposal.proposal_id, base_commit=proposal.base_commit,
            patch_sha256=proposal.sha256, modified_files=paths,
            candidate_worktree=str(candidate), checks=tuple(checks),
            baseline_unchanged=unchanged, failure=failure,
        )

    @staticmethod
    def dispose(repository: str | Path, evaluation: CandidateEvaluation) -> None:
        candidate = Path(evaluation.candidate_worktree).resolve()
        if not candidate.as_posix().startswith("/tmp/openroad-platform-"):
            raise ValueError("Refusing to remove a candidate outside the node-local prefix")
        completed = subprocess.run(
            ["git", "-C", str(Path(repository).resolve()), "worktree", "remove", "--force",
             str(candidate)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=30, check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout.strip())

    @staticmethod
    def _baseline_snapshot(repository: Path) -> tuple[str, str]:
        return (IsolatedCodingAgent._git(repository, "rev-parse", "HEAD"),
                IsolatedCodingAgent._git(repository, "status", "--porcelain=v1", "-uall"))

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        completed = subprocess.run(["git", "-C", str(root), *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, timeout=30, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stdout.strip())
        return completed.stdout.strip()


class PromotionGate:
    """Issue a receipt only. There is intentionally no merge/apply method."""

    @staticmethod
    def review(evaluation: CandidateEvaluation, policy: VerificationPolicy,
               *, human_approved: bool = False) -> dict:
        if evaluation.status != "passed" or not evaluation.baseline_unchanged:
            return {"decision": "rejected", "applied": False,
                    "evaluation_id": evaluation.evaluation_id}
        source = any(not path.startswith(("docs/", "tests/"))
                     for path in evaluation.modified_files)
        if source and policy.require_human_for_source and not human_approved:
            decision = "awaiting_human"
        else:
            decision = "approved_for_manual_promotion"
        return {"decision": decision, "applied": False,
                "evaluation_id": evaluation.evaluation_id,
                "patch_sha256": evaluation.patch_sha256}
