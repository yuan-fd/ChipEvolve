"""Reviewed upstream compatibility backports.

The property that matters is containment: a run patches its own copy of the
flow, never the tree every other run shares, and every step is verified against
a recorded digest rather than assumed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from compatibility import (
    apply_backports,
    CLAIM_BOUNDARY,
    CompatibilityError,
    COMPATIBILITY_FILENAME,
    HEADLESS_FINISH_BACKPORT,
    stage_flow,
    STAGED_FLOW_DIRNAME,
)

OLD = "if {[expr [llength [info procs save_image]] > 0]} {"
NEW = "if {[ord::openroad_gui_compiled] && [llength [info commands gui::show]] > 0} {"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fixture_patch(source: str) -> dict:
    """A patch whose digests describe *source*, so the apply path is testable."""
    return {
        "scope": "test_scope",
        "path": "scripts/final_report.tcl",
        "old": OLD,
        "new": NEW,
        "source_sha256": digest(source),
        "patched_sha256": digest(source.replace(OLD, NEW)),
        "upstream_url": "https://example.invalid/upstream",
        "upstream_commit": "a" * 40,
        "upstream_subject": "a reviewed fix",
        "issue": "https://example.invalid/issue/1",
        "paired_openroad_commit": "b" * 40,
        "paired_openroad_subject": "a paired fix",
        "capability_probe": "info commands gui::show",
    }


def build_flow(root: Path, *, content: str | None = None) -> Path:
    flow = root / "toolchain"
    (flow / "scripts").mkdir(parents=True)
    (flow / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    (flow / "scripts" / "final_report.tcl").write_text(
        content if content is not None else f"header\n{OLD}\nbody\n",
        encoding="utf-8",
    )
    return flow


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------

def test_staging_copies_the_flow_into_the_attempt_workspace(tmp_path):
    flow = build_flow(tmp_path)
    workdir = tmp_path / "attempt"
    workdir.mkdir()
    staged = stage_flow(flow, workdir)
    assert staged == workdir / STAGED_FLOW_DIRNAME
    assert (staged / "Makefile").is_file()
    assert (staged / "scripts" / "final_report.tcl").is_file()


def test_staging_does_not_touch_the_operator_tree(tmp_path):
    """The whole point: a run must not write into the shared toolchain."""
    flow = build_flow(tmp_path)
    before = {p: p.read_bytes() for p in flow.rglob("*") if p.is_file()}
    workdir = tmp_path / "attempt"
    workdir.mkdir()
    stage_flow(flow, workdir)
    after = {p: p.read_bytes() for p in flow.rglob("*") if p.is_file()}
    assert before == after


def test_staging_refuses_a_tree_without_a_makefile(tmp_path):
    empty = tmp_path / "toolchain"
    empty.mkdir()
    with pytest.raises(CompatibilityError, match="no Makefile"):
        stage_flow(empty, tmp_path)


def test_staging_refuses_to_overwrite_an_existing_copy(tmp_path):
    flow = build_flow(tmp_path)
    workdir = tmp_path / "attempt"
    workdir.mkdir()
    stage_flow(flow, workdir)
    with pytest.raises(CompatibilityError, match="already exists"):
        stage_flow(flow, workdir)


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

def test_a_matching_file_is_patched_and_verified(tmp_path):
    flow = build_flow(tmp_path)
    content = (flow / "scripts" / "final_report.tcl").read_text(encoding="utf-8")
    patch = fixture_patch(content)
    records = apply_backports(flow, tmp_path, patch=patch)

    assert len(records) == 1 and records[0].applied is True
    patched = (flow / "scripts" / "final_report.tcl").read_text(encoding="utf-8")
    assert NEW in patched and OLD not in patched
    # The verification is against the outcome, not just the intent.
    assert records[0].patched_sha256 == digest(patched)


def test_a_different_revision_is_left_exactly_as_it_is(tmp_path):
    """The pinned toolchain is one specific revision.

    A newer ORFS already carries the fix, so a digest mismatch is not an error --
    it means there is nothing to do.
    """
    flow = build_flow(tmp_path, content=f"header\n{OLD}\nbody\n")
    patch = fixture_patch("some other content entirely")
    before = (flow / "scripts" / "final_report.tcl").read_bytes()

    records = apply_backports(flow, tmp_path, patch=patch)
    assert records == []
    assert (flow / "scripts" / "final_report.tcl").read_bytes() == before


def test_an_ambiguous_search_string_is_refused(tmp_path):
    """Zero matches means the reviewed source is not what is on disk; more than
    one means the edit cannot be made unambiguously.  Neither may be guessed."""
    for content in (f"{OLD}\n{OLD}\n", "no marker here\n"):
        flow = build_flow(tmp_path / content[:3].replace("{", "x"), content=content)
        patch = fixture_patch(content)
        # Zero matches: the source digest matches but the marker is gone, which
        # cannot happen for a real file and must still be refused rather than
        # produce a silent no-op.
        if content.count(OLD) == 1:
            continue
        with pytest.raises(CompatibilityError, match="ambiguous"):
            apply_backports(flow, tmp_path, patch=patch)


def test_a_result_that_does_not_match_the_reviewed_digest_is_refused(tmp_path):
    flow = build_flow(tmp_path)
    content = (flow / "scripts" / "final_report.tcl").read_text(encoding="utf-8")
    patch = fixture_patch(content)
    patch["patched_sha256"] = "0" * 64
    with pytest.raises(CompatibilityError, match="expected"):
        apply_backports(flow, tmp_path, patch=patch)


def test_a_missing_target_file_is_not_an_error(tmp_path):
    """A tree without the file simply has nothing to patch."""
    flow = tmp_path / "toolchain"
    flow.mkdir()
    (flow / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    records = apply_backports(flow, tmp_path,
                              patch=fixture_patch("anything"))
    assert records == []


# --------------------------------------------------------------------------
# the receipt
# --------------------------------------------------------------------------

def test_the_receipt_is_written_even_when_nothing_was_patched(tmp_path):
    """'No patch was needed' is evidence, not an absence to interpret."""
    flow = build_flow(tmp_path)
    apply_backports(flow, tmp_path, patch=fixture_patch("unrelated"))
    receipt = json.loads(
        (tmp_path / COMPATIBILITY_FILENAME).read_text(encoding="utf-8")
    )
    assert receipt["kind"] == "orfs-flow-compatibility"
    assert receipt["changes"] == []
    assert receipt["claim_boundary"] == CLAIM_BOUNDARY


def test_the_receipt_carries_the_full_provenance_of_a_change(tmp_path):
    flow = build_flow(tmp_path)
    content = (flow / "scripts" / "final_report.tcl").read_text(encoding="utf-8")
    apply_backports(flow, tmp_path, patch=fixture_patch(content))
    change = json.loads(
        (tmp_path / COMPATIBILITY_FILENAME).read_text(encoding="utf-8")
    )["changes"][0]

    for field in ("upstream_url", "upstream_commit", "upstream_subject",
                  "issue", "paired_openroad_commit", "paired_openroad_subject",
                  "capability_probe", "source_sha256", "patched_sha256"):
        assert change[field], field
    # The claim that nothing protected changed is stated, not implied.
    assert change["protected_inputs_changed"] is False


# --------------------------------------------------------------------------
# the real patch data
# --------------------------------------------------------------------------

def test_the_reviewed_patch_records_its_full_provenance():
    for field in ("upstream_url", "upstream_commit", "upstream_subject", "issue",
                  "paired_openroad_commit", "paired_openroad_subject", "path",
                  "source_sha256", "patched_sha256", "old", "new",
                  "capability_probe", "scope"):
        assert HEADLESS_FINISH_BACKPORT[field], field
    assert len(HEADLESS_FINISH_BACKPORT["source_sha256"]) == 64
    assert len(HEADLESS_FINISH_BACKPORT["patched_sha256"]) == 64
    assert len(HEADLESS_FINISH_BACKPORT["upstream_commit"]) == 40


def test_the_reviewed_patch_narrows_the_visualization_guard():
    """The fix replaces a proxy check with a real capability probe.

    The pinned OpenROAD reports GUI support despite exposing no ``gui::show``
    command, so the flow's original check takes the wrong branch.
    """
    assert "info procs save_image" in HEADLESS_FINISH_BACKPORT["old"]
    assert "info commands gui::show" in HEADLESS_FINISH_BACKPORT["new"]
    assert "openroad_gui_compiled" in HEADLESS_FINISH_BACKPORT["new"]
    assert HEADLESS_FINISH_BACKPORT["capability_probe"] == "info commands gui::show"


def test_the_reviewed_patch_touches_only_one_flow_script():
    assert HEADLESS_FINISH_BACKPORT["path"] == "scripts/final_report.tcl"
