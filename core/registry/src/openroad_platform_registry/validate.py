"""Check a plugin against the AgenticEDA plugin protocol.

    python3 -m openroad_platform_registry.validate <plugin-dir> [...]
    openroad-platform-plugin-validate <plugin-dir> [...]

This tool reports; it does not admit.  Whether a plugin may execute on a given
platform is that platform's decision, recorded in its own admissions directory,
and no command-line tool can make it.  What this checks is narrower and
mechanical: whether the files a plugin ships could be accepted by a platform that
follows the protocol.

Two deliberate limits, both stated because a check that quietly does nothing is
worse than no check:

* **Shape, not existence, for an absolute entrypoint.**  A plugin may name an
  interpreter that exists only in its deployment environment
  (``/opt/team-x/venv/bin/python``).  Demanding that file on a developer laptop
  would fail every plugin whose deployment is not the laptop.  A ``./`` entry is
  different: it is resolved inside the plugin directory, so its existence is
  checked.
* **No execution.**  Nothing here runs the adapter.  Running it is what the
  platform's own end-to-end checks do, with a request file and a result file.

It lives in the registry package because the rules it applies are the registry's
own: what a manifest must declare, and what an admission record must say.  A
second implementation of those rules would be a second set of rules, and this one
imports the contract rather than restating it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openroad_platform_contracts import (
    RESERVED_ARTIFACT_KINDS,
    ContractError,
    PluginManifest,
)

from .registry import (
    ADMISSION_SUFFIX,
    PROVENANCE_FILENAME,
    Admission,
    Provenance,
    RegistryError,
)


class Report:
    """Findings, in the order they were found, with a final verdict."""

    def __init__(self, subject: str):
        self.subject = subject
        self.errors: list[str] = []
        self.notes: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"{self.subject}"]
        lines.extend(f"  FAIL  {item}" for item in self.errors)
        lines.extend(f"  note  {item}" for item in self.notes)
        if self.ok:
            lines.append("  ok    complies with the plugin protocol")
        return "\n".join(lines)


def find_manifests(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.plugin.json"))


def check_entry(manifest: PluginManifest, directory: Path, report: Report) -> None:
    """Entrypoint shape, and existence only where existence is knowable."""
    for item in manifest.adapter_entry:
        if item.startswith("./"):
            target = (directory / item[2:]).resolve()
            try:
                target.relative_to(directory.resolve())
            except ValueError:
                report.error(
                    f"adapter_entry {item!r} resolves outside the plugin "
                    f"directory, which the platform refuses"
                )
                continue
            if not target.is_file():
                report.error(f"adapter_entry {item!r} names a file that is absent")
            elif not target.stat().st_mode & 0o111:
                report.note(
                    f"adapter_entry {item!r} is not marked executable; a "
                    f"manifest that runs it through an interpreter does not "
                    f"need this, one that runs it directly does"
                )
        elif item.startswith("../") or "/../" in item:
            report.error(
                f"adapter_entry {item!r} climbs out of the plugin directory, "
                f"which the platform refuses"
            )
    first = manifest.adapter_entry[0] if manifest.adapter_entry else ""
    if first.startswith("/"):
        report.note(
            f"adapter_entry[0] {first!r} is absolute: not checked for "
            f"existence, because it may exist only where the plugin is deployed"
        )


def check_artifact_rules(manifest: PluginManifest, report: Report) -> None:
    """The allowlist itself, not the privilege of using it.

    A manifest MAY list a platform-reserved kind -- the protected evaluator has
    to, because otherwise its own verdict artifact would be refused by its own
    allowlist.  Listing it grants nothing: the authority to *declare* a reserved
    kind is `allow_reserved_artifacts`, which only the platform's evaluation path
    sets.  A validator that rejected the listing would be stricter than the
    platform, and a validator that rejects what the platform accepts is lying.
    """
    reserved = sorted(
        kind for rule in manifest.artifact_rules
        if isinstance(kind := rule.get("kind"), str)
        and kind in RESERVED_ARTIFACT_KINDS
    )
    if reserved:
        report.note(
            f"artifact_rules list platform-reserved kinds ({', '.join(reserved)});"
            f" allowed, and it grants no authority to declare them"
        )
    kinds = [str(rule.get("kind")) for rule in manifest.artifact_rules]
    if len(kinds) != len(set(kinds)):
        report.error("artifact_rules declare the same kind more than once")


def check_provenance(directory: Path, report: Report) -> None:
    path = directory / PROVENANCE_FILENAME
    if not path.is_file():
        report.note(
            f"no {PROVENANCE_FILENAME}: the plugin states no origin. That is "
            f"allowed, and a reviewer will have less to go on"
        )
        return
    try:
        Provenance.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, RegistryError) as exc:
        report.error(f"{PROVENANCE_FILENAME} is not valid provenance: {exc}")


def check_admission(
    plugin_id: str, admissions_root: Path | None, report: Report
) -> None:
    """Report the platform's decision.  Never make one."""
    if admissions_root is None:
        report.note(
            "no --admissions-root given, so admission was not examined; a "
            "platform decides that, not this tool"
        )
        return
    path = admissions_root / f"{plugin_id}{ADMISSION_SUFFIX}"
    if not path.is_file():
        report.note(
            f"no admission record at {path}: the plugin is discoverable and "
            f"will refuse to execute until a platform reviews it"
        )
        return
    try:
        admission = Admission.from_dict(
            plugin_id, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, RegistryError) as exc:
        report.error(f"admission record is not admissible: {exc}")
        return
    report.note(
        f"admission record: status={admission.status} "
        f"licence_review={admission.license_review} "
        f"reviewer={admission.reviewer}"
    )
    if admission.status != "admitted":
        report.note("the platform will list this plugin and refuse to run it")


def validate_directory(directory: Path, admissions_root: Path | None) -> Report:
    report = Report(str(directory))
    if not directory.is_dir():
        report.error("not a directory")
        return report

    manifests = find_manifests(directory)
    if not manifests:
        report.error("no *.plugin.json in this directory")
        return report
    if len(manifests) > 1:
        report.note(
            f"{len(manifests)} manifests in one directory; a platform registers "
            f"each, and a plugin_id with several versions must be disambiguated "
            f"by the caller"
        )

    for manifest_path in manifests:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.error(f"{manifest_path.name} is not readable JSON: {exc}")
            continue
        try:
            manifest = PluginManifest.from_dict(payload)
        except ContractError as exc:
            report.error(f"{manifest_path.name}: {exc}")
            continue
        report.note(
            f"{manifest_path.name}: {manifest.plugin_id}"
            f"@{manifest.plugin_version}, capabilities "
            f"{', '.join(manifest.capabilities)}"
        )
        check_entry(manifest, directory, report)
        check_artifact_rules(manifest, report)
        check_admission(manifest.plugin_id, admissions_root, report)

    check_provenance(directory, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugins", nargs="+", type=Path,
                        help="plugin directories to check")
    parser.add_argument("--admissions-root", type=Path, default=None,
                        help="report the platform's decision for each plugin")
    args = parser.parse_args(argv)

    reports = [validate_directory(d, args.admissions_root) for d in args.plugins]
    print("\n".join(report.render() for report in reports))
    failed = [report for report in reports if not report.ok]
    if failed:
        print(f"\n{len(failed)} of {len(reports)} plugin(s) do not comply")
        return 1
    print(f"\n{len(reports)} plugin(s) comply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
