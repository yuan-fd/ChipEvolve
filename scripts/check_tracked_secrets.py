#!/usr/bin/env python3
"""Fail if Git-tracked blobs contain common plaintext credential signatures."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "provider_secret": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "assigned_secret": re.compile(
        rb"(?i)\b(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\b"
        rb"\s*[:=]\s*['\"][^'\"\r\n]{12,}['\"]"
    ),
}
SUSPICIOUS_NAMES = re.compile(
    r"(^|/)(?:\.env(?:\..*)?|id_(?:rsa|ed25519)|credentials(?:\..*)?|.*\.pem|.*\.key)$",
    re.I,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    files = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z"]
    ).split(b"\0")
    findings = []
    suspicious_names = []
    scanned = 0
    for raw in files:
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        if SUSPICIOUS_NAMES.search(relative):
            suspicious_names.append(relative)
        path = ROOT / relative
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content[:8192]:
            continue
        scanned += 1
        for name, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append({"file": relative, "signature": name})
    payload = {
        "schema_version": 1, "tracked_files_scanned": scanned,
        "credential_findings": findings,
        "suspicious_tracked_filenames": suspicious_names,
        "passed": not findings and not suspicious_names,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
