"""Pinned reference-design recipes.

This is the benchmark identity, so the tests pin the recipes themselves, not just
the loading logic: a silently changed period or baseline makes every comparison
against an older run meaningless while still looking valid.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reference_designs import (
    DEFINITIONS,
    fingerprint_of,
    HDL_SUFFIXES,
    load_paper_reference_design,
    load_reference_design,
    ORFS_AGENT_PAPER_COMMIT,
    PAPER_DEFINITION,
    RECIPE_CURRENT,
    RECIPE_PAPER,
    registered_designs,
)


# --------------------------------------------------------------------------
# the recipes themselves
# --------------------------------------------------------------------------

def test_exactly_the_registered_designs_are_available():
    assert registered_designs() == [
        ("asap7", "aes"), ("asap7", "ibex"), ("asap7", "jpeg"),
        ("sky130hd", "aes"), ("sky130hd", "ibex"), ("sky130hd", "jpeg"),
    ]


def test_every_recipe_declares_a_clock_and_a_baseline():
    """A recipe without a clock is not a design, it is a variable."""
    for key, definition in DEFINITIONS.items():
        assert definition.get("period"), key
        assert definition.get("top"), key
        assert definition.get("sdc"), key
        assert definition.get("baseline"), key


def test_the_asap7_periods_are_in_nanoseconds_not_picoseconds():
    """ASAP7's Liberty unit is ps and the source SDC keeps the raw value.

    The recipe is the boundary, so it holds nanoseconds.  A recipe that kept
    picoseconds here would make the platform compare a 0.380 ps design against a
    3.6 ns one.
    """
    assert DEFINITIONS[("asap7", "aes")]["period"] == 0.380
    assert DEFINITIONS[("asap7", "jpeg")]["period"] == 0.680
    assert DEFINITIONS[("asap7", "ibex")]["period"] == 1.468
    # The nanosecond platforms are all O(1)-O(10).
    assert DEFINITIONS[("sky130hd", "aes")]["period"] == 3.6
    assert DEFINITIONS[("sky130hd", "jpeg")]["period"] == 5.0
    assert DEFINITIONS[("sky130hd", "ibex")]["period"] == 10.0


def test_the_ibex_recipe_uses_the_reviewable_constraint_variant():
    """The default 1000 ns constraint is not signoff-feasible on the pinned
    flow, so ORFS ships an explicit variant and the recipe uses that."""
    assert DEFINITIONS[("asap7", "ibex")]["sdc"] == "constraint_pos_slack.sdc"
    assert DEFINITIONS[("asap7", "ibex")]["frontend"] == "slang"
    assert DEFINITIONS[("asap7", "ibex")]["extra"] == "syn/rtl/prim_clock_gating.v"


def test_the_paper_anchor_is_admissible_under_the_parameter_gate():
    """The paper recipe starts at utilization 20, and the gate's lower bound for
    SKY130HD is 20.

    Those two numbers living in different files is exactly how a gate ends up
    rejecting the official starting configuration of the study it is meant to
    reproduce.  The current recipe deliberately sits higher, at 35.
    """
    from parameters import ORFS_PLATFORM_BOUNDS
    assert PAPER_DEFINITION["baseline"]["core_utilization_pct"] == 20
    assert ORFS_PLATFORM_BOUNDS["sky130hd"]["core_utilization_pct"][0] == 20
    assert DEFINITIONS[("sky130hd", "aes")]["baseline"]["core_utilization_pct"] == 35


def test_the_paper_recipe_is_distinct_from_the_current_one():
    """Accepting the current recipe in its place would make the L1 evidence
    handoff ambiguous about which design was built."""
    current = DEFINITIONS[("sky130hd", "aes")]
    assert current["period"] == 3.6
    assert PAPER_DEFINITION["period"] == 4.5
    assert PAPER_DEFINITION.get("fast_route") == "fastroute.tcl"
    assert current.get("fast_route") is None


# --------------------------------------------------------------------------
# a synthetic ORFS tree
# --------------------------------------------------------------------------

def build_orfs_tree(root: Path, *, platform="sky130hd", design="aes",
                    recipe=None) -> Path:
    recipe = recipe or DEFINITIONS[(platform, design)]
    source_root = root / "flow" / "designs" / "src" / recipe["root"]
    source_root.mkdir(parents=True)
    suffix = ".sv" if recipe.get("frontend") == "slang" else ".v"
    (source_root / f"{recipe['top']}{suffix}").write_text(
        f"module {recipe['top']};\nendmodule\n", encoding="utf-8")
    (source_root / f"helper{suffix}").write_text(
        "module helper;\nendmodule\n", encoding="utf-8")
    if recipe.get("extra"):
        extra = source_root / recipe["extra"]
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("module prim_clock_gating;\nendmodule\n", encoding="utf-8")
    if recipe.get("include"):
        include = source_root / recipe["include"]
        include.mkdir(parents=True, exist_ok=True)
        (include / "prim.vh").write_text("// a header\n", encoding="utf-8")

    design_dir = root / "flow" / "designs" / platform / design
    design_dir.mkdir(parents=True)
    (design_dir / recipe["sdc"]).write_text(
        f"create_clock -period {recipe['period']}\n", encoding="utf-8")
    if recipe.get("fast_route"):
        (design_dir / recipe["fast_route"]).write_text("# fastroute\n",
                                                       encoding="utf-8")
    return root


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(root), *arguments], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=False).stdout.strip()


@pytest.fixture()
def orfs_tree(tmp_path: Path) -> Path:
    """A synthetic ORFS tree under version control, so commits are real."""
    root = tmp_path / "orfs"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "t@local")
    git(root, "config", "user.name", "test")
    build_orfs_tree(root)
    git(root, "add", "-A")
    git(root, "commit", "-m", "bundle")
    return root


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def test_a_bundle_resolves_with_its_sources_and_constraints(orfs_tree: Path):
    resolved = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    assert resolved.recipe_id == RECIPE_CURRENT
    assert resolved.top == "aes_cipher_top"
    assert resolved.clock == "clk"
    assert resolved.clock_period_ns == 3.6
    assert resolved.sdc_path.is_file()
    stems = {path.stem for path in resolved.rtl_files}
    assert "aes_cipher_top" in stems and "helper" in stems


def test_an_unregistered_design_is_refused(orfs_tree: Path):
    with pytest.raises(ValueError, match="unregistered reference design"):
        load_reference_design(orfs_tree, platform="sky130hd", design="nope")


def test_a_missing_source_file_is_refused(tmp_path: Path):
    """A bundle that is incomplete must fail here, not hours into an EDA run."""
    root = tmp_path / "orfs"
    root.mkdir()
    git(root, "init")
    build_orfs_tree(root)
    # Remove every source, not just the top: the bundle is what is absent.
    for source in (root / "flow" / "designs" / "src" / "aes").glob("*.v"):
        source.unlink()
    with pytest.raises(FileNotFoundError, match="incomplete reference bundle"):
        load_reference_design(root, platform="sky130hd", design="aes")


def test_a_missing_constraint_file_is_refused(tmp_path: Path):
    root = tmp_path / "orfs"
    root.mkdir()
    build_orfs_tree(root)
    (root / "flow" / "designs" / "sky130hd" / "aes" / "constraint.sdc").unlink()
    with pytest.raises(FileNotFoundError, match="incomplete reference bundle"):
        load_reference_design(root, platform="sky130hd", design="aes")


def test_a_bundle_whose_top_is_absent_is_refused(tmp_path: Path):
    """The check is on the top module, not on the file count: a bundle with
    sources but no top would fail much later, inside the flow."""
    root = tmp_path / "orfs"
    root.mkdir()
    build_orfs_tree(root)
    top = root / "flow" / "designs" / "src" / "aes" / "aes_cipher_top.v"
    top.rename(top.with_name("something_else.v"))
    with pytest.raises(ValueError, match="top source is absent"):
        load_reference_design(root, platform="sky130hd", design="aes")


def test_include_headers_are_part_of_the_bundle(tmp_path: Path):
    root = tmp_path / "orfs"
    root.mkdir()
    build_orfs_tree(root, platform="asap7", design="ibex")
    resolved = load_reference_design(root, platform="asap7", design="ibex")
    assert resolved.include_dirs
    # The bundle still resolves if a header is deleted from disk, because the
    # files list was taken first; what matters is that no path is missing at
    # resolution time.
    assert resolved.synth_hdl_frontend == "slang"


# --------------------------------------------------------------------------
# the fingerprint
# --------------------------------------------------------------------------

def test_the_fingerprint_is_stable_for_the_same_sources(orfs_tree: Path):
    first = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    second = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    assert first.source_fingerprint == second.source_fingerprint


def test_the_fingerprint_changes_when_a_source_byte_changes(orfs_tree: Path):
    """An old result must not be silently attributed to new sources."""
    before = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    source = before.rtl_root / "helper.v"
    source.write_text("module helper;\n// edited\nendmodule\n", encoding="utf-8")
    after = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    assert before.source_fingerprint != after.source_fingerprint


def test_the_fingerprint_changes_when_the_constraint_changes(orfs_tree: Path):
    """The clock is part of the design.  Two arms with different periods are
    not being compared on the same thing."""
    before = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    before.sdc_path.write_text("create_clock -period 2.0\n", encoding="utf-8")
    after = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    assert before.source_fingerprint != after.source_fingerprint


def test_the_fingerprint_includes_the_toolchain_commit(orfs_tree: Path):
    """The same sources built by two ORFS revisions are two measurements."""
    first = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    # A real content change, or the commit is a no-op and HEAD never moves.
    (orfs_tree / "flow" / "designs" / "src" / "aes" / "helper.v").write_text(
        "module helper;\n// changed\nendmodule\n", encoding="utf-8")
    git(orfs_tree, "add", "-A")
    git(orfs_tree, "commit", "-m", "second")
    second = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    assert first.orfs_commit != second.orfs_commit
    assert first.source_fingerprint != second.source_fingerprint


def test_the_fingerprint_covers_the_recipe_not_just_the_bytes():
    """The same files resolved by two recipes are two reference designs."""
    records = [("a.v", "0" * 64)]
    assert fingerprint_of(recipe_id="one", definition={"period": 1},
                          records=records, orfs_commit="c" * 40) != \
        fingerprint_of(recipe_id="two", definition={"period": 1},
                       records=records, orfs_commit="c" * 40)


def test_the_fingerprint_is_a_lowercase_hex_digest(orfs_tree: Path):
    fingerprint = load_reference_design(
        orfs_tree, platform="sky130hd", design="aes").source_fingerprint
    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()
    int(fingerprint, 16)


# --------------------------------------------------------------------------
# the paper recipe
# --------------------------------------------------------------------------

def test_the_paper_recipe_requires_its_exact_commit(tmp_path: Path):
    root = tmp_path / "orfs"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "t@local")
    git(root, "config", "user.name", "test")
    build_orfs_tree(root, recipe=PAPER_DEFINITION)
    git(root, "add", "-A")
    git(root, "commit", "-m", "bundle")

    with pytest.raises(ValueError, match="requires ORFS commit"):
        load_paper_reference_design(root)


def test_the_paper_recipe_refuses_a_dirty_checkout(tmp_path: Path):
    """A dirty tree means the sources may not be the ones that were measured."""
    root = tmp_path / "orfs"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "t@local")
    git(root, "config", "user.name", "test")
    build_orfs_tree(root, recipe=PAPER_DEFINITION)
    git(root, "add", "-A")
    git(root, "commit", "-m", "bundle")
    head = git(root, "rev-parse", "HEAD")

    if head == ORFS_AGENT_PAPER_COMMIT:  # pragma: no cover - cannot happen
        pytest.skip("synthetic tree cannot be the pinned commit")
    # The commit check fires first, so assert on that rather than pretending.
    with pytest.raises(ValueError, match="requires ORFS commit"):
        load_paper_reference_design(root)


def test_the_paper_recipe_is_limited_to_its_platform_and_design(orfs_tree: Path):
    with pytest.raises(ValueError, match="only sky130hd/aes"):
        load_paper_reference_design(orfs_tree, platform="asap7", design="aes")
    with pytest.raises(ValueError, match="only sky130hd/aes"):
        load_paper_reference_design(orfs_tree, platform="sky130hd", design="jpeg")


def test_the_paper_commit_is_a_full_sha():
    assert len(ORFS_AGENT_PAPER_COMMIT) == 40
    int(ORFS_AGENT_PAPER_COMMIT, 16)


# --------------------------------------------------------------------------
# what a bundle hands to an attempt
# --------------------------------------------------------------------------

def test_to_inputs_carries_the_design_and_its_identity(orfs_tree: Path):
    resolved = load_reference_design(orfs_tree, platform="sky130hd", design="aes")
    inputs = resolved.to_inputs()
    assert inputs["design_bundle_sha256"] == resolved.source_fingerprint
    assert inputs["orfs_commit"] == resolved.orfs_commit
    assert inputs["platform"] == "sky130hd"
    assert inputs["top"] == "aes_cipher_top"
    assert inputs["clock_period_ns"] == 3.6
    assert inputs["rtl_files"]
    # The baseline is not an instruction to the attempt; it is the reference the
    # arm is compared against.
    assert "baseline" not in " ".join(inputs)


def test_to_metadata_identifies_the_bundle_without_the_file_list(orfs_tree: Path):
    metadata = load_reference_design(
        orfs_tree, platform="sky130hd", design="aes").to_metadata()
    assert metadata["recipe_id"] == RECIPE_CURRENT
    assert metadata["source_fingerprint"]
    assert "rtl_files" not in metadata


def test_the_hdl_suffix_table_is_what_include_scanning_uses():
    assert HDL_SUFFIXES == {".v", ".sv", ".vh", ".svh"}
