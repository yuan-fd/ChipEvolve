"""Versioned fixed RTL evaluation packages for v2 admission and regression."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


REQUIRED = ("gcd", "fifo", "uart_tx", "ibex_alu")


def suite_root() -> Path:
    return Path(__file__).resolve().parents[4] / "benchmarks" / "v2"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_design_package(name: str, *, root: Path | None = None) -> dict[str, Any]:
    package = (root or suite_root()) / name
    manifest_path = package / "package.json"
    if not manifest_path.is_file():
        raise KeyError(f"Unknown v2 design package: {name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = ("name", "spec", "testbench", "golden_rtl", "top", "tier")
    if any(not isinstance(manifest.get(key), str) or not manifest[key] for key in required):
        raise ValueError("Invalid design package manifest")
    files = {key: package / manifest[key] for key in ("spec", "testbench", "golden_rtl")}
    if not all(path.is_file() for path in files.values()):
        raise ValueError("Design package is incomplete")
    if manifest["name"] != name or name not in REQUIRED:
        raise ValueError("Design package is not in the v2 fixed suite")
    return {
        **manifest, "root": str(package),
        "hashes": {key: _sha(path) for key, path in files.items()},
        "contents": {key: path.read_text(encoding="utf-8") for key, path in files.items()},
    }


def list_design_packages(*, root: Path | None = None) -> list[dict[str, Any]]:
    result = []
    for name in REQUIRED:
        package = load_design_package(name, root=root)
        result.append({key: package[key] for key in ("name", "top", "tier", "description", "hashes")})
    return result
