#!/usr/bin/env python3
"""Recheck DPLEvolve patches against the fixed clean OpenROAD archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=(
        ROOT / ".tools/dplevolve-anchors/downloads/openroad-d14d526.tar.gz"
    ))
    parser.add_argument("--patch-root", type=Path,
                        default=ROOT / ".external-src/dplevolve/patches")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve()
    patches = args.patch_root.resolve()
    expected_archive_sha = "4441e13fe45bd63d520e63f74484f3f0db6569e5be181ea0274ff3ad0e877dbe"
    if _sha256(archive) != expected_archive_sha:
        raise ValueError("Fixed OpenROAD archive SHA-256 mismatch")

    base = patches / "openroad_dpl_evolve_base.patch"
    framework = patches / "openroad_dpl_evolve_framework.patch"
    evolved = patches / "evolved_legalizers"
    with tempfile.TemporaryDirectory(prefix="p15-patch-check-") as temporary:
        root = Path(temporary)
        with tarfile.open(archive, "r:gz") as stream:
            # The fixed upstream archive contains convenience links that point
            # outside its root.  Patch checking does not need them, so omit all
            # links while retaining Python's traversal/device protections.
            stream.extractall(root, filter=_safe_archive_member)
        directories = [item for item in root.iterdir() if item.is_dir()]
        if len(directories) != 1:
            raise ValueError("OpenROAD archive has an unexpected root layout")
        source = directories[0]
        _run(("git", "init", "-q"), source, check=True)
        _run(("git", "add", "-A"), source, check=True)
        _run(("git", "-c", "user.name=P15", "-c", "user.email=p15@invalid",
              "commit", "-qm", "fixed clean boundary"), source, check=True)
        cases = []
        cases.append(_check_case(source, "base-from-clean", (base,), True))
        cases.append(_check_case(source, "framework-after-base", (base, framework), True))
        cases.append(_check_case(
            source, "diamond-from-clean",
            (evolved / "openroad_dpl_evolve_diamond_iter30_from_clean.patch",), True,
        ))
        cases.append(_check_case(
            source, "negotiation-from-clean",
            (evolved / "openroad_dpl_evolve_negotiation_iter30_from_clean.patch",), True,
        ))
        cases.append(_check_case(
            source, "diamond-framework-delta",
            (base, framework,
             evolved / "openroad_dpl_evolve_diamond_iter30_framework_delta.patch"), False,
        ))
        cases.append(_check_case(
            source, "negotiation-framework-delta",
            (base, framework,
             evolved / "openroad_dpl_evolve_negotiation_iter30_framework_delta.patch"), False,
        ))
    payload = {
        "schema_version": 1, "phase": "P15", "check": "fixed-anchor-patches",
        "openroad_commit": "d14d526a6f8ce5388e2a8dc30da88a0189df2f46",
        "openroad_archive_sha256": expected_archive_sha,
        "cases": cases,
        "expected_successes_passed": all(
            item["observed_applies"] for item in cases if item["expected_applies"]
        ),
        "known_delta_mismatches_preserved": all(
            not item["observed_applies"] for item in cases if not item["expected_applies"]
        ),
    }
    payload["accepted"] = (payload["expected_successes_passed"]
                           and payload["known_delta_mismatches_preserved"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["accepted"] else 2


def _check_case(source: Path, name: str, patch_chain: tuple[Path, ...],
                expected_applies: bool) -> dict:
    _run(("git", "reset", "--hard", "-q", "HEAD"), source, check=True)
    _run(("git", "clean", "-fdx", "-q"), source, check=True)
    failure = ""
    observed = True
    for index, patch in enumerate(patch_chain):
        result = _run(("git", "apply", "--check", str(patch)), source)
        if result.returncode:
            observed = False
            failure = result.stdout[-4000:]
            break
        if index < len(patch_chain) - 1:
            applied = _run(("git", "apply", str(patch)), source)
            if applied.returncode:
                observed = False
                failure = applied.stdout[-4000:]
                break
    return {
        "name": name, "expected_applies": expected_applies,
        "observed_applies": observed,
        "expectation_met": observed == expected_applies,
        "patches": [{"path": str(item.relative_to(ROOT)), "sha256": _sha256(item)}
                    for item in patch_chain],
        "failure_excerpt": failure,
    }


def _run(argv: tuple[str, ...], cwd: Path, *, check: bool = False):
    result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stdout)
    return result


def _safe_archive_member(member: tarfile.TarInfo, path: str):
    if member.issym() or member.islnk():
        return None
    return tarfile.data_filter(member, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
