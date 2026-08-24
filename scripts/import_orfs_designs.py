#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0: import ORFS typical designs into the platform design library.

Reads RTL from ~/OpenROAD-flow-scripts/flow/designs/src/<design>/ (multi-file
designs are concatenated), registers each via DesignService.import_rtl, and
marks it with a "[ORFS]" description tag so the web examples API can surface
it as a typical design.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src",
               ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source))

from apps.api.app import ApiState  # noqa: E402

ORFS_SRC = Path.home() / "OpenROAD-flow-scripts" / "flow" / "designs" / "src"
OWNER = "user-ea0c3d1f4520448d99a22d2dc7f7b250"  # yuanwenjie (live)

# design -> (top module, skip patterns for testbench/aux files)
DESIGNS = {
    "gcd":      ("gcd",          ("_tb",)),
    "aes":      ("aes_cipher_top", ("_tb",)),
    "ethmac":   ("ethmac",       ("_tb",)),
    "ibex_sv":  ("ibex_core",    ("_tb",)),
}


def collect_rtl(design: str, top: str, skip: tuple) -> str:
    src = ORFS_SRC / design
    if not src.is_dir():
        raise FileNotFoundError(f"ORFS design dir missing: {src}")
    files = sorted(p for p in src.iterdir()
                   if p.suffix in (".v", ".sv") and not any(
                       token in p.name for token in skip))
    if not files:
        raise FileNotFoundError(f"No RTL files for {design} in {src}")
    # Header/define files must come first so macros are defined before use;
    # the design-named file (usually the top module, e.g. ethmac.v) comes next
    # so _module_name picks the real top; the rest keep alphabetical order.
    files = sorted(files, key=lambda p: (
        0 if "defines" in p.name else
        1 if p.stem == design else
        2, p.name))
    parts = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        # Inline `include directives: the included file's content is already
        # concatenated below (or the include is a timescale header), so the
        # include line itself would break a standalone read.
        text = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("`include"))
        parts.append(f"// ---- {f.name} ----\n" + text)
    return "\n".join(parts)


def main() -> int:
    state = ApiState(
        ROOT / "var" / "platform.db", ROOT / "var" / "uploads",
        ROOT.parent / "OpenROAD-flow-scripts",
        design_root=ROOT / "var" / "designs", legacy_root=ROOT.parent / "iccad",
        runtime_db_path=ROOT / "var" / "public" / "runtime.db",
        optimization_db_path=ROOT / "var" / "public" / "optimization.db",
        auth_db_path=ROOT / "var" / "public" / "web-auth.db",
 load_taiwei_plugin=False,
    )
    # skip designs already imported with the ORFS tag
    existing = {d["id"] for d in state.designs.list(limit=100)}
    existing_desc = {d["id"]: d.get("description", "") for d in state.designs.list(limit=100)}
    imported = []
    for design, (top, skip) in DESIGNS.items():
        if design in existing and str(existing_desc.get(design, "")).startswith("[ORFS]"):
            print(f"skip {design}: already imported")
            continue
        try:
            source = collect_rtl(design, top, skip)
        except FileNotFoundError as exc:
            print(f"skip {design}: {exc}")
            continue
        try:
            rec = state.designs.import_rtl(
                filename=f"{design}.v", source=source,
                description=f"[ORFS] {design} · typical design (top={top})",
                owner_id=OWNER)
            imported.append((design, rec["id"], rec.get("module")))
            print(f"+ {design} -> {rec['id']} module={rec.get('module')}")
        except Exception as exc:
            print(f"! {design} import failed: {type(exc).__name__}: {str(exc)[:200]}")
    print(f"imported={len(imported)}")
    return 0 if imported else 1


if __name__ == "__main__":
    raise SystemExit(main())
