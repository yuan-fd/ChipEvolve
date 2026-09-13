"""The toolchain snapshot: which tools produced this measurement.

Ported from the frozen v1 implementation.  A fingerprint of the toolchain and a
record of every file and request input is what lets a stored result be attributed
to something reproducible.  Without it, "the same experiment" is a phrase with no
referent.

Three details are decisions rather than mechanics:

* **The worktree status is recorded, not enforced.**  A snapshot is evidence, so
  a dirty ORFS checkout is written down as dirty.  (The paper reference-design
  loader does refuse a dirty tree, because there the dirty checkout would change
  which sources were built.  Different question, different answer.)
* **A missing file still produces a record naming it.**  ``sha256: None`` with a
  path says "this was expected and is not there"; omitting the key would say
  nothing at all.
* **Environment *names* are recorded, not values.**  Which variables were
  inherited matters for reproducibility; their contents can hold credentials, and
  a snapshot that leaked them would be worse than one that omitted them.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from digest import sha256_file

TOOLCHAIN_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1

#: Host variables a toolchain profile passes through.  EDA tools need the
#: library and Tcl paths; a snapshot that dropped them would describe an
#: environment that cannot run the flow.
DEFAULT_INHERITED_ENVIRONMENT: tuple[str, ...] = (
    "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP",
    "LD_LIBRARY_PATH", "LIBRARY_PATH", "TCL_LIBRARY", "TK_LIBRARY",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)

#: Final fallback for PATH, so a profile that sets nothing still resolves the
#: system tools the flow shells out to.
SYSTEM_PATH: tuple[str, ...] = (
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
)

#: How long a version probe may take before it is reported as unknown.  A tool
#: that hangs on ``-version`` must not hang the run that is trying to describe
#: itself.
PROBE_TIMEOUT_SECONDS = 15


def _deduplicate(values: list[str]) -> list[str]:
    """Keep the first occurrence of each entry, in order.

    PATH order is precedence, so the first is the one that wins; a later
    duplicate is noise that makes the recorded PATH misleading.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


@dataclass(frozen=True)
class ToolchainConfig:
    name: str
    orfs_root: Path
    openroad_bin: Path
    yosys_bin: Path
    klayout_bin: Path | None = None
    extra_path: tuple[Path, ...] = ()
    inherit_environment: tuple[str, ...] = DEFAULT_INHERITED_ENVIRONMENT
    environment: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Refuse a profile that cannot run, naming what is wrong.

        The name is checked for whitespace because it is written into the flow's
        environment and into the snapshot; it is an identifier, not prose.
        Executability is checked here rather than left to ``make``, because a
        flow that starts and then fails on an unlaunchable tool reports the
        failure at the wrong layer, hours later, as a stage error.

        Absolute paths are checked too.  A relative entry on PATH resolves
        against the *child's* working directory, so the flow could otherwise
        launch an executable out of its own attempt workspace.
        """
        if not self.name or any(char.isspace() for char in self.name):
            raise ValueError(f"invalid toolchain name: {self.name!r}")
        for label, path in (("orfs_root", self.orfs_root),
                            ("openroad_bin", self.openroad_bin),
                            ("yosys_bin", self.yosys_bin)):
            if not Path(path).is_absolute():
                raise ValueError(f"{label} must be an absolute path: {path}")
        makefile = self.flow_home / "Makefile"
        if not makefile.is_file():
            raise FileNotFoundError(f"ORFS Makefile not found: {makefile}")
        for label, binary in (("OpenROAD", self.openroad_bin),
                              ("Yosys", self.yosys_bin),
                              ("KLayout", self.klayout_bin)):
            if binary is None:
                continue
            if not Path(binary).is_file() or not os.access(binary, os.X_OK):
                raise FileNotFoundError(f"{label} executable not found: {binary}")

    @property
    def flow_home(self) -> Path:
        """Where the flow's Makefile lives: ``<orfs_root>/flow``.

        There is exactly one layout.  An earlier version of this module also
        accepted a checkout that kept the Makefile at its root, "rather than
        guess" -- but accepting two layouts meant the recorded ``orfs_root`` no
        longer said which one a run used, and it forced a helper whose only job
        was to invert the ambiguity.  The real checkout is the checkout; a path
        that is not one is an error naming it.
        """
        return Path(self.orfs_root) / "flow"

    def build_environment(self, *, source: Mapping[str, str] | None = None,
                          extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """The environment an attempt runs in.

        PATH is composed rather than inherited: the tool's own directories come
        first, so a run cannot accidentally pick up a different build of the same
        tool from the host.
        """
        host = source if source is not None else os.environ
        environment = {key: host[key] for key in self.inherit_environment
                       if key in host}
        environment.setdefault("HOME", str(Path.home()))

        paths = [str(Path(self.openroad_bin).parent),
                 str(Path(self.yosys_bin).parent)]
        if self.klayout_bin is not None:
            paths.append(str(Path(self.klayout_bin).parent))
        paths.extend(str(path) for path in self.extra_path)
        paths.extend((str(Path.home() / ".local" / "bin"), *SYSTEM_PATH))
        environment["PATH"] = os.pathsep.join(_deduplicate(paths))

        environment.update({str(k): str(v) for k, v in self.environment.items()})
        if extra:
            environment.update({str(k): str(v) for k, v in extra.items()})
        environment["OPENROAD_PLATFORM_TOOLCHAIN"] = self.name
        return environment

    def fingerprint(self) -> str:
        """A stable identity for this profile.

        Paths and environment keys go in; environment *values* do not, so the
        fingerprint can be stored and compared without carrying a secret.
        """
        payload = {
            "name": self.name,
            "orfs_root": str(self.orfs_root),
            "openroad_bin": str(self.openroad_bin),
            "yosys_bin": str(self.yosys_bin),
            "klayout_bin": str(self.klayout_bin) if self.klayout_bin else None,
            "extra_path": [str(path) for path in self.extra_path],
            "inherit_environment": list(self.inherit_environment),
            "environment_keys": sorted(self.environment),
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": TOOLCHAIN_SCHEMA_VERSION,
            "name": self.name,
            "orfs_root": str(self.orfs_root),
            "openroad_bin": str(self.openroad_bin),
            "yosys_bin": str(self.yosys_bin),
            "klayout_bin": str(self.klayout_bin) if self.klayout_bin else None,
            "extra_path": [str(path) for path in self.extra_path],
            "inherit_environment": list(self.inherit_environment),
            # Names, not values.  Which variables were available is
            # reproducibility; their contents can be credentials.
            "environment_keys": sorted(self.environment),
            "fingerprint": self.fingerprint(),
        }


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------

def probe_version(command: list[str],
                  timeout: float = PROBE_TIMEOUT_SECONDS) -> str | None:
    """The tool's first non-empty version line, or None.

    A tool that cannot be probed is reported as unknown rather than as an empty
    string, so a reader can tell "we could not ask" from "it said nothing".
    """
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((line.strip() for line in result.stdout.splitlines()
                 if line.strip()), None)


def command_lines(command: list[str],
                  timeout: float = PROBE_TIMEOUT_SECONDS) -> list[str]:
    """All non-empty output lines, or ``["unknown"]``.

    The literal ``unknown`` is returned rather than an empty list so that a
    missing git, a timeout and a genuinely clean tree are three distinguishable
    outcomes in the record.
    """
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["unknown"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def file_record(path: str | Path | None) -> dict[str, Any] | None:
    """Identify a file, or say that an expected one is missing.

    ``None`` means no file was expected.  A record with a path and a null hash
    means one was expected and is absent -- and naming it is the whole point,
    because "the SDC was not found" is a different fact from "no SDC applies".
    """
    if path is None:
        return None
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        return {"path": str(resolved), "size_bytes": None, "sha256": None}
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def toolchain_snapshot(
    config: ToolchainConfig, *, workdir: str | Path,
    request: Mapping[str, Any], rtl_path: str | Path,
    rtl_files: list[str] | None = None,
    generated_config: str | Path | None = None,
    platform_config: str | Path | None = None,
    sdc_path: str | Path | None = None,
    fast_route_tcl: str | Path | None = None,
) -> dict[str, Any]:
    """Everything needed to attribute a result to a toolchain and a request."""
    workdir = Path(workdir).expanduser().resolve()
    versions = {
        "openroad": probe_version([str(config.openroad_bin), "-version"]),
        "yosys": probe_version([str(config.yosys_bin), "-V"]),
        "orfs_commit": probe_version(
            ["git", "-C", str(config.orfs_root), "rev-parse", "HEAD"]),
    }
    if config.klayout_bin is not None:
        versions["klayout"] = probe_version([str(config.klayout_bin), "-v"])

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "toolchain": config.snapshot(),
        "versions": versions,
        # Recorded, not enforced: a snapshot is evidence.
        "orfs_worktree_status": command_lines(
            ["git", "-C", str(config.orfs_root), "status", "--porcelain=v1"]),
        "files": {
            "openroad": file_record(config.openroad_bin),
            "yosys": file_record(config.yosys_bin),
            "klayout": file_record(config.klayout_bin),
            "platform_config": file_record(platform_config),
            "generated_config": file_record(generated_config),
            "flow_compatibility_receipt": file_record(
                workdir / "flow_compatibility.json"),
            "rtl": file_record(rtl_path),
            "rtl_bundle": [file_record(item) for item in (rtl_files or [])],
            "sdc": file_record(sdc_path),
            "fast_route_tcl": file_record(fast_route_tcl),
        },
        "request": dict(request),
    }


def resolve_from_environment(
    *, name: str, orfs_root: str | Path | None = None,
    openroad_bin: str | Path | None = None, yosys_bin: str | Path | None = None,
    klayout_bin: str | Path | None = None,
    extra_path: tuple[str | Path, ...] = (),
    environment: Mapping[str, str] | None = None,
) -> ToolchainConfig:
    """Build a profile from explicit paths or the environment.

    An explicit path always wins, so a caller that knows where its toolchain is
    does not have to depend on the host environment agreeing.

    A variable that is unset is an error, not a default.  The frozen
    implementation fell back to ``~/OpenROAD-flow-scripts`` and
    ``~/bin/openroad``; that is a silent substitution of a toolchain nobody
    named, and the snapshot would then attribute the result to a profile the
    operator never chose.  The variable names are v1's, so an operator's
    existing environment keeps working.
    """
    def pick(explicit: str | Path | None, variable: str) -> Path:
        if explicit is not None:
            return Path(explicit).expanduser().resolve()
        value = os.environ.get(variable)
        if not value:
            raise ValueError(
                f"{variable} is not set and no explicit path was given"
            )
        return Path(value).expanduser().resolve()

    root = pick(orfs_root, "ORFS_ROOT")
    return ToolchainConfig(
        name=name, orfs_root=root,
        openroad_bin=pick(openroad_bin, "OPENROAD_BIN"),
        yosys_bin=pick(yosys_bin, "YOSYS_BIN"),
        klayout_bin=(Path(klayout_bin).expanduser().resolve()
                     if klayout_bin is not None
                     else (Path(os.environ["KLAYOUT_BIN"]).expanduser().resolve()
                           if os.environ.get("KLAYOUT_BIN") else None)),
        extra_path=tuple(Path(item).expanduser().resolve() for item in extra_path),
        environment=dict(environment or {}),
    )
