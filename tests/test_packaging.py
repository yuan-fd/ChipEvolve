"""Packaging: every declared dependency is real, and every real one is declared.

A dependency list that does not match the imports is worse than an empty one.  It
is a claim about how the software is assembled, and the three application
packages carried a false one for several rounds: they declared the contracts
package while importing the client, which no check noticed because the guardrail
for apps asks only that a `pyproject.toml` exists.

This test asks the harder question, for every package in the tree.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Importable platform module -> the distribution that provides it.
DISTRIBUTIONS = {
    "openroad_platform_contracts": "openroad-platform-contracts",
    "openroad_platform_runtime": "openroad-platform-runtime",
    "openroad_platform_registry": "openroad-platform-registry",
    "openroad_platform_evaluator": "openroad-platform-evaluator",
    "openroad_platform_provenance": "openroad-platform-provenance",
    "openroad_platform_identity": "openroad-platform-identity",
    "openroad_platform_client": "openroad-platform-client",
    "openroad_platform_gateway": "openroad-platform-gateway",
}

#: Every package that ships a pyproject, as (directory, source root).
PACKAGES = [
    (REPO_ROOT / "contracts", "src"),
    (REPO_ROOT / "core" / "runtime", "src"),
    (REPO_ROOT / "core" / "registry", "src"),
    (REPO_ROOT / "core" / "evaluator", "src"),
    (REPO_ROOT / "core" / "provenance", "src"),
    (REPO_ROOT / "core" / "identity", "src"),
    (REPO_ROOT / "core" / "client", "src"),
    (REPO_ROOT / "gateway", "src"),
    (REPO_ROOT / "apps" / "evidence_console", "src"),
    (REPO_ROOT / "apps" / "dse_lab", "src"),
    (REPO_ROOT / "apps" / "plan_executor", "src"),
    (REPO_ROOT / "apps" / "query_agent", "src"),
    (REPO_ROOT / "apps" / "run_console", "src"),
]


def declared_dependencies(package: Path) -> list[str]:
    """The `[project] dependencies` array, read without a TOML parser.

    Python 3.9 is a supported platform for this repository -- the aarch64 build
    host runs it -- and it has no ``tomllib``, which the first version of this
    test imported.  A check that cannot run on a supported platform is a check
    that does not check.

    It understands the one shape these files use, and raises on anything else:
    a file it cannot read must fail loudly rather than be reported as declaring
    no dependencies at all.
    """
    pyproject = package / "pyproject.toml"
    assert pyproject.is_file(), f"{package} has no pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    project = re.search(r"^\[project\]\s*$", text, re.MULTILINE)
    assert project, f"{pyproject} has no [project] table"
    rest = text[project.end() :]
    # Stop at the next table header, so a later section's keys are not read here.
    next_table = re.search(r"^\[", rest, re.MULTILINE)
    body = rest[: next_table.start()] if next_table else rest
    match = re.search(
        r"^dependencies\s*=\s*\[(.*?)\]",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert match, (
        f"{pyproject} does not state `dependencies` explicitly; write `[]` "
        f"rather than leaving it implied"
    )
    return re.findall(r'"([^"]+)"', match.group(1))


def platform_imports(package: Path, source_root: str) -> set[str]:
    """Top-level platform modules this package actually imports."""
    found: set[str] = set()
    for path in (package / source_root).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in DISTRIBUTIONS:
                    found.add(root)
    return found


@pytest.mark.parametrize(
    "package,source_root", PACKAGES, ids=[p[0].name for p in PACKAGES]
)
def test_declared_dependencies_match_the_imports(package: Path, source_root: str):
    declared = set(declared_dependencies(package))
    imported = {DISTRIBUTIONS[name] for name in platform_imports(package, source_root)}

    # A package never depends on itself.
    own = DISTRIBUTIONS.get(
        next(
            (
                module
                for module, dist in DISTRIBUTIONS.items()
                if package.name.replace("-", "_") in module
            ),
            "",
        ),
        None,
    )
    imported.discard(own)

    missing = sorted(imported - declared)
    extra = sorted(declared - imported)

    assert not missing, (
        f"{package.name} imports {missing} but does not declare "
        f"{'them' if len(missing) > 1 else 'it'}"
    )
    assert not extra, (
        f"{package.name} declares {extra} but does not import "
        f"{'them' if len(extra) > 1 else 'it'}"
    )


def test_every_package_has_a_pyproject():
    """The contracts package was the missing one, and eight declared it."""
    for package, _ in PACKAGES:
        assert (package / "pyproject.toml").is_file(), package


def test_the_declared_names_match_the_package_names():
    for package, _ in PACKAGES:
        text = (package / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
        assert match, package
        assert match.group(1).startswith("openroad-"), (package, match.group(1))
