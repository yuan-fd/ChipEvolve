"""Frozen design adapters for the v2 paper experiments.

The original four SpecIR/RTLScout tasks stay in the suite.  AES, JPEG,
RISC-V32I and SPI come from the pinned ORFS checkout so the backend study is
not limited to toy-size generated modules.  Multi-file Verilog is expanded
into one immutable source artifact because the current platform contract
content-addresses one RTL input per run.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FIXED_DESIGNS = ("gcd", "fifo", "uart_tx", "ibex_alu")
ORFS_DESIGNS = ("aes", "jpeg", "spi", "uart")
PAPER_DESIGNS = FIXED_DESIGNS + ORFS_DESIGNS


@dataclass(frozen=True)
class PaperDesign:
    name: str
    top: str
    clock: str
    clock_period_ns: float
    filename: str
    source: str
    source_kind: str


_ORFS = {
    "aes": ("aes_cipher_top", "clk", 2.0),
    "jpeg": ("jpeg_encoder", "clk", 2.5),
    "spi": ("spi", "clk", 4.0),
    "uart": ("uart", "clk", 4.0),
}


def load_paper_design(name: str, orfs_root: Path) -> PaperDesign:
    if name in FIXED_DESIGNS:
        package = ROOT / "benchmarks" / "v2" / name
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        return PaperDesign(
            name=name, top=manifest["top"], clock=manifest.get("clock") or "clk",
            clock_period_ns=10.0, filename=manifest["golden_rtl"],
            source=(package / manifest["golden_rtl"]).read_text(encoding="utf-8"),
            source_kind="v2-fixed-suite-golden-rtl",
        )
    if name not in _ORFS:
        raise ValueError(f"unknown paper design: {name}")
    top, clock, period = _ORFS[name]
    source_root = orfs_root.expanduser().resolve() / "flow" / "designs" / "src" / name
    if not source_root.is_dir():
        raise FileNotFoundError(f"ORFS design source is missing: {source_root}")
    files = sorted(source_root.rglob("*.v"))
    by_name = {path.name: path for path in files}
    module_pattern = re.compile(rf"^\s*module\s+{re.escape(top)}\b", re.MULTILINE)
    top_file = next((path for path in files
                     if module_pattern.search(path.read_text(encoding="utf-8", errors="replace"))), None)
    if top_file is None:
        raise ValueError(f"top module {top} not found for {name}")
    included: set[Path] = set()

    def expand(path: Path, stack: tuple[Path, ...] = ()) -> str:
        lines: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("`include") and '"' in stripped:
                include_name = stripped.split('"', 2)[1]
                target = by_name.get(include_name)
                if target is not None and target not in stack:
                    included.add(target)
                    lines.append(f"// expanded include {include_name}")
                    lines.append(expand(target, stack + (path,)))
                    continue
            lines.append(line)
        return "\n".join(lines)

    # Put the top declaration first so DesignService records the intended top;
    # Verilog permits dependent module declarations later in the same file.
    ordered = [top_file] + [path for path in files if path != top_file]
    expanded = []
    for path in ordered:
        if path in included:
            continue
        expanded.append(f"// ORFS source: {path.relative_to(source_root)}\n{expand(path)}")
    return PaperDesign(
        name=name, top=top, clock=clock, clock_period_ns=period,
        filename=f"{name}.v", source="\n\n".join(expanded) + "\n",
        source_kind="pinned-orfs-multifile-expanded",
    )
