"""Reviewed upstream compatibility backports, applied to the attempt copy only.

Ported from the frozen v1 implementation, including the exact digests.  Two
properties make this safe rather than merely convenient:

* **It only ever touches the Runtime-owned attempt copy.**  The operator's ORFS
  tree is shared and read-only to a run; patching it in place would change the
  toolchain under every other experiment.
* **Every step is verified against a recorded digest.**  The patch applies only
  if the file's hash matches the reviewed source, the search string must occur
  exactly once, and the result must hash to the reviewed patched digest.  A
  different ORFS revision is therefore left alone rather than mangled.

The pinned ORFS/OpenROAD pair predates two coordinated upstream fixes.  The
pinned OpenROAD reports GUI support despite exposing no ``gui::show`` command,
so the flow's own headless check takes the wrong branch and the final report
step fails.  The upstream fix is in the pinned ORFS' own history but the pinned
commit predates it, so it is backported here rather than by moving the pin --
moving the pin would change every measurement in the study.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from digest import sha256_file

#: Name of the staged flow inside the attempt workspace.
STAGED_FLOW_DIRNAME = "orfs-flow"

#: Name of the receipt recording what, if anything, was changed.
COMPATIBILITY_FILENAME = "flow_compatibility.json"

#: The one admitted backport.  Every field is provenance: which upstream commit
#: carries the fix, which issue it addresses, which OpenROAD commit the fix was
#: paired with, and the exact bytes before and after.
HEADLESS_FINISH_BACKPORT: dict[str, Any] = {
    "scope": "optional_headless_finish_visualization_guard",
    "upstream_url": "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts",
    "upstream_commit": "e7a0725758c0abde2b69e2cdba783435238748ef",
    "upstream_subject": "Correctly check for OR compiled w/o the GUI enabled.",
    "issue": "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/issues/3050",
    "paired_openroad_commit": "4630b597e7da45019e0e17f19bc58f9128bdbc03",
    "paired_openroad_subject": "ord: correct the setting of BUILD_PYTHON & BUILD_GUI",
    "path": "scripts/final_report.tcl",
    "source_sha256": "431bbf48065fa534369e78fb846390e8b32226800311c2c927afe27e63f8e7b2",
    "patched_sha256": "19e52ae48ac8116a4c451973561e3103b537a330f4e7a5cc14c40d2278708204",
    "old": "if {[expr [llength [info procs save_image]] > 0]} {",
    "new": ("if {[ord::openroad_gui_compiled] && "
            "[llength [info commands gui::show]] > 0} {"),
    "capability_probe": "info commands gui::show",
}

CLAIM_BOUNDARY = (
    "Reviewed upstream compatibility backports applied only to the Runtime-owned "
    "attempt copy; RTL, PDK, SDC, evaluator, metrics, and optimizer semantics "
    "are unchanged."
)


class CompatibilityError(RuntimeError):
    """A backport could not be applied as reviewed.  Never ignored."""


@dataclass(frozen=True)
class BackportRecord:
    """What was changed, and the evidence that it was changed as reviewed."""

    path: str
    source_sha256: str
    patched_sha256: str
    applied: bool

    def to_dict(self) -> dict[str, Any]:
        record = {
            "kind": "upstream_backport",
            "scope": HEADLESS_FINISH_BACKPORT["scope"],
            "path": self.path,
            "source_sha256": self.source_sha256,
            "patched_sha256": self.patched_sha256,
            "upstream_url": HEADLESS_FINISH_BACKPORT["upstream_url"],
            "upstream_commit": HEADLESS_FINISH_BACKPORT["upstream_commit"],
            "upstream_subject": HEADLESS_FINISH_BACKPORT["upstream_subject"],
            "issue": HEADLESS_FINISH_BACKPORT["issue"],
            "paired_openroad_commit": HEADLESS_FINISH_BACKPORT["paired_openroad_commit"],
            "paired_openroad_subject": HEADLESS_FINISH_BACKPORT["paired_openroad_subject"],
            "capability_probe": HEADLESS_FINISH_BACKPORT["capability_probe"],
            # Stated explicitly rather than left to be inferred: none of the
            # protected inputs changed.
            "protected_inputs_changed": False,
        }
        return record


def stage_flow(flow_home: str | Path, workdir: str | Path) -> Path:
    """Copy the flow into the attempt workspace.

    Symlinks are preserved, so a tree that keeps its large build outputs behind
    links does not have them materialised per attempt.  The copy is what ``make``
    runs in: a run must not be able to write into the tree every other run
    shares.
    """
    source = Path(flow_home).expanduser().resolve()
    if not (source / "Makefile").is_file():
        raise CompatibilityError(f"no Makefile under {source}")
    staged = Path(workdir).expanduser().resolve() / STAGED_FLOW_DIRNAME
    if staged.exists():
        raise CompatibilityError(f"staged flow already exists: {staged}")
    shutil.copytree(source, staged, symlinks=True)
    return staged


def apply_backports(
    staged_flow: str | Path, workdir: str | Path, *,
    patch: dict[str, Any] | None = None,
) -> list[BackportRecord]:
    """Apply the admitted backports and write the compatibility receipt.

    A file whose digest does not match is left exactly as it is: the pinned
    toolchain is one specific revision, and a different one has not been
    reviewed.  That is deliberately not an error -- a newer ORFS already carries
    the fix.

    ``patch`` defaults to the one reviewed backport.  It exists so the apply
    and verify logic can be exercised against a fixture: the production
    digests describe one exact upstream revision and cannot be reproduced by
    inventing a file.
    """
    flow = Path(staged_flow).expanduser().resolve()
    patch = patch if patch is not None else HEADLESS_FINISH_BACKPORT
    relative = Path(str(patch["path"]))
    target = flow / relative
    records: list[BackportRecord] = []

    if target.is_file() and sha256_file(target) == patch["source_sha256"]:
        original = target.read_text(encoding="utf-8")
        old = str(patch["old"])
        occurrences = original.count(old)
        if occurrences != 1:
            # Zero would mean the reviewed source was already changed; more than
            # one would mean the edit is ambiguous.  Neither may be guessed at.
            raise CompatibilityError(
                f"reviewed backport source is ambiguous: {occurrences} matches "
                f"of the search string in {relative}"
            )
        target.write_text(original.replace(old, str(patch["new"])), encoding="utf-8")
        actual = sha256_file(target)
        if actual != patch["patched_sha256"]:
            raise CompatibilityError(
                f"backport produced {actual}, expected {patch['patched_sha256']}"
            )
        records.append(BackportRecord(
            path=str(relative), source_sha256=str(patch["source_sha256"]),
            patched_sha256=actual, applied=True,
        ))

    write_receipt(workdir, records)
    return records


def write_receipt(workdir: str | Path, records: list[BackportRecord]) -> Path:
    """Record what the run changed about the toolchain, or that it changed nothing.

    Written even when nothing was patched, so "no patch was needed" is evidence
    rather than an absence a reader has to interpret.
    """
    import json

    path = Path(workdir).expanduser().resolve() / COMPATIBILITY_FILENAME
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "orfs-flow-compatibility",
        "changes": [record.to_dict() for record in records],
        "claim_boundary": CLAIM_BOUNDARY,
    }, indent=2), encoding="utf-8")
    return path
