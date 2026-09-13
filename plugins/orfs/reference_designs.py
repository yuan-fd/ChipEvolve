"""Pinned source-bundle recipes for the reference designs.

Ported from the frozen v1 implementation.  A recipe identifies *source bytes*
and elaboration requirements.  Optimizer logic is deliberately absent: every
optimizer arm receives the same bundle, differing only in its calibrated
flow-parameter vector and seed.

Three properties make a comparison meaningful, and all three are enforced here
rather than assumed:

* **The clock is part of the recipe, not a tunable.**  Two arms that could each
  choose their own period are not being compared on the same design.
* **The fingerprint covers the recipe, every source file's bytes, and the
  toolchain commit.**  A bundle that changed anywhere produces a different
  fingerprint, so an old result cannot be silently attributed to new sources.
* **The paper recipe is a separate recipe, not a flag.**  It pins a different
  ORFS commit with a different SDC, and accepting the current recipe in its
  place would make the evidence handoff from L1 to L2 ambiguous about which
  design was actually built.

One unit conversion lives in the table: ASAP7's Liberty/SDC unit is
picoseconds, while every platform-facing contract is nanoseconds.  The source
SDC is left byte-identical -- it still carries the official raw value -- and the
conversion happens at the boundary, which is the same rule the stage-JSON
parser follows on the way back out.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from digest import sha256_file

#: Recipe identities.  Recorded in the fingerprint, so the same bundle resolved
#: by two different recipes is two different reference designs.
RECIPE_CURRENT = "orfs-current-reference-v1"
RECIPE_PAPER = "orfs-agent-paper-aes-sky130hd-v1"

#: The ORFS commit the paper comparison was run against.
ORFS_AGENT_PAPER_COMMIT = "ce8d36a7fef0ab9c47d183bcf078bce0f60f5a54"

#: Source-file suffixes that make up a bundle.
HDL_SUFFIXES = frozenset({".v", ".sv", ".vh", ".svh"})


@dataclass(frozen=True)
class ReferenceDesign:
    """One resolved bundle: sources, constraints, and the identity of both."""

    recipe_id: str
    platform: str
    design: str
    top: str
    clock: str
    clock_period_ns: float
    rtl_root: Path
    rtl_files: tuple[Path, ...]
    include_dirs: tuple[Path, ...]
    sdc_path: Path
    synth_hdl_frontend: str | None
    design_options: Mapping[str, Any]
    native_baseline_overrides: Mapping[str, Any]
    source_fingerprint: str
    orfs_commit: str
    fast_route_tcl_path: Path | None = None

    def to_inputs(self) -> dict[str, Any]:
        """The bundle as task inputs.

        Everything an attempt needs to reproduce the design, and nothing about
        how to optimize it -- that belongs to the arm, not the design.
        """
        return {
            "rtl_root": str(self.rtl_root),
            "rtl_files": [str(path) for path in self.rtl_files],
            "rtl_include_dirs": [str(path) for path in self.include_dirs],
            "sdc_path": str(self.sdc_path),
            "top": self.top,
            "clock": self.clock,
            "clock_period_ns": self.clock_period_ns,
            "platform": self.platform,
            "design": self.design,
            "reference_design": self.design,
            "design_bundle_sha256": self.source_fingerprint,
            "orfs_commit": self.orfs_commit,
            **({"synth_hdl_frontend": self.synth_hdl_frontend}
               if self.synth_hdl_frontend else {}),
            **({"fast_route_tcl_path": str(self.fast_route_tcl_path)}
               if self.fast_route_tcl_path else {}),
            **({"design_options": dict(self.design_options)}
               if self.design_options else {}),
        }

    def to_metadata(self) -> dict[str, Any]:
        """The identity of this bundle, without the file list."""
        return {
            "recipe_id": self.recipe_id,
            "platform": self.platform,
            "design": self.design,
            "top": self.top,
            "clock_period_ns": self.clock_period_ns,
            "source_fingerprint": self.source_fingerprint,
            "orfs_commit": self.orfs_commit,
            "native_baseline_overrides": dict(self.native_baseline_overrides),
        }


#: Registered recipes, keyed by (platform, design).
DEFINITIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("asap7", "aes"): {
        "root": "aes", "top": "aes_cipher_top", "clock": "clk",
        # ASAP7's Liberty/SDC unit is ps.  Public contracts and the index are
        # ns; the source SDC stays byte-identical and still contains the
        # official raw value 380 ps.
        "sdc": "constraint.sdc", "period": 0.380,
        "baseline": {"core_utilization_pct": 70, "place_density": .65,
                     "tns_end_percent": 100},
    },
    ("sky130hd", "aes"): {
        "root": "aes", "top": "aes_cipher_top", "clock": "clk",
        "sdc": "constraint.sdc", "period": 3.6,
        "options": {"remove_abc_buffers": 1, "swap_arith_operators": 1,
                    "openroad_hierarchical": 1},
        "baseline": {"core_utilization_pct": 35, "place_density_lb_addon": .2,
                     "tns_end_percent": 100},
    },
    ("asap7", "jpeg"): {
        "root": "jpeg", "top": "jpeg_encoder", "clock": "clk",
        "sdc": "jpeg_encoder15_7nm.sdc", "period": 0.680, "include": "include",
        "baseline": {"core_utilization_pct": 70, "place_density": .75,
                     "tns_end_percent": 100},
    },
    ("sky130hd", "jpeg"): {
        "root": "jpeg", "top": "jpeg_encoder", "clock": "clk",
        "sdc": "constraint.sdc", "period": 5.0, "include": "include",
        "options": {"remove_abc_buffers": 1},
        "baseline": {"core_utilization_pct": 55, "place_density_lb_addon": .15,
                     "tns_end_percent": 100},
    },
    ("asap7", "ibex"): {
        "root": "ibex_sv", "top": "ibex_core", "clock": "clk_i",
        # ORFS ships this explicit, reviewable variant because its default
        # 1000 ns constraint is not signoff-feasible on the pinned flow.  Every
        # optimizer arm receives these same bytes; the clock is never tuned.
        "sdc": "constraint_pos_slack.sdc", "period": 1.468,
        "include": "vendor/lowrisc_ip/prim/rtl", "frontend": "slang",
        "extra": "syn/rtl/prim_clock_gating.v",
        "options": {"swap_arith_operators": 1, "openroad_hierarchical": 1},
        "baseline": {"core_utilization_pct": 40, "place_density_lb_addon": .2,
                     "enable_dpo": 0, "tns_end_percent": 100},
    },
    ("sky130hd", "ibex"): {
        "root": "ibex_sv", "top": "ibex_core", "clock": "clk_i",
        "sdc": "constraint.sdc", "period": 10.0,
        "include": "vendor/lowrisc_ip/prim/rtl", "frontend": "slang",
        "extra": "syn/rtl/prim_clock_gating.v",
        "options": {"remove_abc_buffers": 1, "swap_arith_operators": 1,
                    "openroad_hierarchical": 1},
        "baseline": {"core_utilization_pct": 50, "place_density_lb_addon": .25,
                     "tns_end_percent": 100},
    },
}

#: The fixed-clock recipe the paper comparison used, before variable-clock DSE.
#: Deliberately separate from the current sky130hd/aes definition, whose SDC is
#: 3.6 ns rather than 4.5 ns.
PAPER_DEFINITION: dict[str, Any] = {
    "root": "aes", "top": "aes_cipher_top", "clock": "clk",
    "sdc": "constraint.sdc", "period": 4.5,
    "fast_route": "fastroute.tcl",
    "options": {"remove_abc_buffers": 1},
    "baseline": {"core_utilization_pct": 20, "place_density": .6,
                 "tns_end_percent": 100},
}


def registered_designs() -> list[tuple[str, str]]:
    return sorted(DEFINITIONS)


def load_reference_design(orfs_root: str | Path, *, platform: str,
                          design: str) -> ReferenceDesign:
    root = Path(orfs_root).expanduser().resolve()
    definition = DEFINITIONS.get((platform, design))
    if definition is None:
        raise ValueError(f"unregistered reference design: {platform}/{design}")
    return _resolve(root, platform=platform, design=design,
                    definition=definition, recipe_id=RECIPE_CURRENT)


def load_paper_reference_design(
    orfs_root: str | Path, *, platform: str = "sky130hd", design: str = "aes",
) -> ReferenceDesign:
    """Load the exact fixed-clock reference used before variable-clock DSE.

    Requires the pinned commit *and* a clean checkout.  A dirty tree means the
    sources may not be the ones the comparison was run against, and the
    fingerprint would then describe something that was never measured.
    """
    if (platform, design) != ("sky130hd", "aes"):
        raise ValueError("the paper reference is only sky130hd/aes")
    root = Path(orfs_root).expanduser().resolve()
    commit = _git(root, "rev-parse", "HEAD")
    if commit != ORFS_AGENT_PAPER_COMMIT:
        raise ValueError(
            f"the paper reference requires ORFS commit {ORFS_AGENT_PAPER_COMMIT}; "
            f"observed {commit or 'unknown'}"
        )
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("the paper reference requires a clean checkout")
    return _resolve(root, platform=platform, design=design,
                    definition=PAPER_DEFINITION, recipe_id=RECIPE_PAPER,
                    expected_commit=ORFS_AGENT_PAPER_COMMIT)


def _resolve(root: Path, *, platform: str, design: str,
             definition: Mapping[str, Any], recipe_id: str,
             expected_commit: str | None = None) -> ReferenceDesign:
    source_root = root / "flow" / "designs" / "src" / str(definition["root"])
    suffix = "*.sv" if definition.get("frontend") == "slang" else "*.v"
    files = tuple(sorted(source_root.glob(suffix)))
    if definition.get("extra"):
        files += (source_root / str(definition["extra"]),)
    include_dirs = ((source_root / str(definition["include"]),)
                    if definition.get("include") else ())
    sdc = root / "flow" / "designs" / platform / design / str(definition["sdc"])
    fast_route = (root / "flow" / "designs" / platform / design /
                  str(definition["fast_route"])
                  if definition.get("fast_route") else None)

    required = (*files, *include_dirs, sdc,
                *((fast_route,) if fast_route else ()))
    if not files or any(not path.exists() for path in required):
        raise FileNotFoundError(
            f"incomplete reference bundle: {platform}/{design}"
        )
    if not any(path.stem == definition["top"] for path in files):
        # The top module must be in the sources; a bundle whose top is missing
        # would fail much later, inside a long EDA run.
        raise ValueError(
            f"top source is absent from the bundle: {definition['top']}"
        )

    records: list[tuple[str, str]] = [
        (str(path.relative_to(root)), sha256_file(path)) for path in files
    ]
    for directory in include_dirs:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in HDL_SUFFIXES:
                records.append((str(path.relative_to(root)), sha256_file(path)))
    records.append((str(sdc.relative_to(root)), sha256_file(sdc)))
    if fast_route is not None:
        records.append((str(fast_route.relative_to(root)), sha256_file(fast_route)))

    commit = _git(root, "rev-parse", "HEAD") or "unknown"
    if expected_commit is not None and commit != expected_commit:
        # The checkout moved while the bundle was being hashed, so the
        # fingerprint would describe a mixture of two revisions.
        raise ValueError("the reference checkout changed during resolution")

    fingerprint = fingerprint_of(
        recipe_id=recipe_id, definition=definition, records=records,
        orfs_commit=commit,
    )
    return ReferenceDesign(
        recipe_id=recipe_id, platform=platform, design=design,
        top=str(definition["top"]), clock=str(definition["clock"]),
        clock_period_ns=float(definition["period"]), rtl_root=source_root,
        rtl_files=files, include_dirs=include_dirs, sdc_path=sdc,
        synth_hdl_frontend=definition.get("frontend"),
        design_options=dict(definition.get("options") or {}),
        native_baseline_overrides=dict(definition["baseline"]),
        source_fingerprint=fingerprint, orfs_commit=commit,
        fast_route_tcl_path=fast_route,
    )


def fingerprint_of(*, recipe_id: str, definition: Mapping[str, Any],
                   records: list[tuple[str, str]],
                   orfs_commit: str) -> str:
    """Identify a bundle by recipe, every source byte, and the toolchain commit.

    All three go in.  A recipe alone would not notice an edited source; the
    sources alone would not notice a different toolchain, and the same sources
    built by two ORFS revisions are two different measurements.
    """
    import hashlib

    payload = {"recipe_id": recipe_id, "definition": definition,
               "files": records, "orfs_commit": orfs_commit}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    ).stdout.strip()
