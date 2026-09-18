"""Guardrail rule engine.

Every architectural rule in AGENTS.md is implemented here as a pure function
that scans a repository root and returns a list of violations.  Each rule has:

  * a positive check  - the real tree must produce ZERO violations
  * a negative fixture - guardrails/negative/<rule>/ must produce >= 1 violation

The negative fixture is the point.  A gate that cannot be shown to fail is not
a gate; it is a comment.  ``test_g00_gates_are_capable_of_failing`` enforces
that every rule actually fires on its own fixture.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Repository layout the rules are written against
# --------------------------------------------------------------------------

CORE_DIR = "core"
CONTRACTS_DIR = "contracts"
APP_DIR = "apps"
PLUGIN_DIR = "plugins"
GATEWAY_DIR = "gateway"

#: Directories that make up the platform kernel.  Nothing here may know a
#: concrete plugin, tool, or vendor name.
#:
#: ``contracts`` is included deliberately.  It is the shared language every
#: layer depends on, so a vendor name there would propagate everywhere -- and
#: a contract that mentions a tool has stopped being generic.
KERNEL_DIRS = (CORE_DIR, CONTRACTS_DIR, GATEWAY_DIR)

SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv",
    "venv", "guardrails", ".mypy_cache", ".ruff_cache", "build", "dist",
    ".eggs",
}


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    line: int
    detail: str

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"[{self.rule}] {self.path}:{self.line}: {self.detail}"


def _walk_python(root: Path):
    """Yield every .py file under *root* outside vendored/skip directories."""
    for path in sorted(root.rglob("*.py")):
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIR_NAMES:
            continue
        yield path


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _loc(path: Path) -> int:
    return len(_read(path).splitlines())


# --------------------------------------------------------------------------
# G1 - the kernel must not name a concrete plugin, tool, or vendor
# --------------------------------------------------------------------------

#: Concrete capability / tool / vendor identifiers.  A thin control plane may
#: not know any of these.  Add to this list, never remove from it: removing an
#: entry silently re-opens a hole.
#:
#: These are unambiguous: none of them is an ordinary English word, so a hit is
#: always a reference to the thing itself, in code or in prose.
FORBIDDEN_KERNEL_TOKENS: tuple[str, ...] = (
    "orfs", "orfs_agent", "orfs-agent", "a2_orfo", "a2-orfo",
    "rtlscout", "rtl_scout", "agenticpd", "edacraft", "implcraft",
    "dplevolve", "orassistant", "posteda", "closer_bench", "statetune",
    "taiwei", "sky130", "nangate45", "asap7",
    "openroad", "yosys", "verilator", "klayout", "iverilog", "opensta",
    "netgen",
)

#: Tool names that are also ordinary English words.  "make" is a build tool and
#: a verb; "magic" is a layout tool and a noun.  Checking these in prose produces
#: false positives, and a gate that cries wolf gets switched off -- the same way
#: v1's flaky timeout test taught people to ignore a red safety suite.
#:
#: They are therefore checked only where they would actually couple the kernel
#: to a tool: in code and in string literals.  A comment that says "make the
#: path relative" is not a dependency.
AMBIGUOUS_KERNEL_TOKENS: tuple[str, ...] = ("make", "magic")

#: The platform's own namespace.  The project is named after the tool it
#: orchestrates, so the kernel will inevitably contain that token -- in its own
#: package names.  That is the platform naming itself, which is not the failure
#: mode this rule exists to catch.  Every other appearance is.
PLATFORM_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "openroad_platform",
    "openroad-platform",
)

#: Identifiers are matched whole, so ``openroad-platform-runtime`` is seen as one
#: identifier and can be exempted by prefix rather than split on the hyphen into
#: a bare vendor token.
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")

_TOKEN_RE = {
    t: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(t)}(?![A-Za-z0-9_])", re.I)
    for t in FORBIDDEN_KERNEL_TOKENS + AMBIGUOUS_KERNEL_TOKENS
}


def _is_platform_namespace(identifier: str) -> bool:
    lowered = identifier.lower()
    return any(lowered.startswith(prefix) for prefix in PLATFORM_NAMESPACE_PREFIXES)


def _enclosing_identifier(line: str, start: int, end: int) -> str:
    for match in _IDENTIFIER_RE.finditer(line):
        if match.start() <= start and end <= match.end():
            return match.group(0)
    return line[start:end]


def _docstring_lines(tree: ast.AST) -> set[int]:
    """Line numbers occupied by a module, class, or function docstring."""
    lines: set[int] = set()
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, owners):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            constant = first.value
            end = getattr(constant, "end_lineno", None) or constant.lineno
            lines.update(range(constant.lineno, end + 1))
    return lines


def kernel_plugin_name_violations(root: Path) -> list[Violation]:
    """G1: kernel source must not mention a concrete plugin, tool, or vendor.

    Unambiguous vendor tokens are checked everywhere, prose included: a kernel
    that must name a vendor to explain itself is still coupled to that vendor.
    Tokens that are also ordinary English words are checked only in code and
    string literals, so the rule stays precise enough to be trusted.
    """
    out: list[Violation] = []
    for d in KERNEL_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for path in _walk_python(base):
            text = _read(path)
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            prose_lines = _docstring_lines(tree)
            for lineno, line in enumerate(text.splitlines(), 1):
                marker = line.find("#")
                code = line if marker < 0 else line[:marker]
                comment = "" if marker < 0 else line[marker:]
                # Code and string literals are checked for every token.
                # Prose -- a comment or a docstring -- is checked only for
                # unambiguous vendor names, so an English sentence is not
                # mistaken for a dependency on a tool called "make".
                if lineno in prose_lines:
                    scopes = [(line, False)]
                else:
                    scopes = [(code, True)]
                    if comment:
                        scopes.append((comment, False))
                for segment, allow_ambiguous in scopes:
                    if not segment:
                        continue
                    for token, rx in _TOKEN_RE.items():
                        if not allow_ambiguous and token in AMBIGUOUS_KERNEL_TOKENS:
                            continue
                        for match in rx.finditer(segment):
                            identifier = _enclosing_identifier(
                                segment, match.start(), match.end()
                            )
                            if _is_platform_namespace(identifier):
                                continue
                            out.append(Violation(
                                "G1", _rel(root, path), lineno,
                                f"kernel names concrete token {token!r} "
                                f"in identifier {identifier!r}",
                            ))
    return out


# --------------------------------------------------------------------------
# G2 - the kernel must not carry adapter / plugin implementation files
# --------------------------------------------------------------------------

_ADAPTER_FILE_RE = re.compile(r"(_adapter|_plugin)\.py$")


def kernel_adapter_file_violations(root: Path) -> list[Violation]:
    """G2: no *_plugin.py / *_adapter.py may live in the kernel."""
    out: list[Violation] = []
    for d in KERNEL_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for path in _walk_python(base):
            if _ADAPTER_FILE_RE.search(path.name):
                out.append(Violation(
                    "G2", _rel(root, path), 0,
                    "adapter/plugin implementation file inside the kernel",
                ))
    return out


# --------------------------------------------------------------------------
# G3 / G4 - application import direction
# --------------------------------------------------------------------------

def _imported_modules(path: Path) -> list[tuple[str, int]]:
    """Return (dotted module, line) for every import in *path*."""
    try:
        tree = ast.parse(_read(path), filename=str(path))
    except SyntaxError:
        return []
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.append((node.module, node.lineno))
                for alias in node.names:
                    found.append((f"{node.module}.{alias.name}", node.lineno))
    return found


def _app_dirs(root: Path) -> list[Path]:
    base = root / APP_DIR
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def app_cross_import_violations(root: Path) -> list[Violation]:
    """G3: one app must never import another app."""
    out: list[Violation] = []
    names = [d.name for d in _app_dirs(root)]
    for app in _app_dirs(root):
        others = [n for n in names if n != app.name]
        for path in _walk_python(app):
            for module, lineno in _imported_modules(path):
                parts = module.split(".")
                # apps.<other>.…  or  openroad_app_<other>
                if len(parts) >= 2 and parts[0] == APP_DIR and parts[1] in others:
                    out.append(Violation(
                        "G3", _rel(root, path), lineno,
                        f"app {app.name!r} imports sibling app {parts[1]!r}",
                    ))
                for other in others:
                    if parts[0] in (f"openroad_app_{other}",
                                    f"openroad_app_{other.replace('_', '')}"):
                        out.append(Violation(
                            "G3", _rel(root, path), lineno,
                            f"app {app.name!r} imports sibling app {other!r}",
                        ))
    return out


#: The only import surfaces an app may use to reach the platform.
APP_ALLOWED_PLATFORM_IMPORTS = (
    "openroad_platform_contracts",   # the shared language
    "openroad_platform_client",  # the typed client for the kernel service
)


def app_forbidden_core_import_violations(root: Path) -> list[Violation]:
    """G4: an app may only import contracts + the core client, not kernel innards."""
    out: list[Violation] = []
    for app in _app_dirs(root):
        for path in _walk_python(app):
            # Integration tests may assemble the in-process gateway and worker
            # to verify the public boundary.  They are not application runtime
            # code; production modules remain subject to this rule.
            if path.name.startswith("test_"):
                continue
            for module, lineno in _imported_modules(path):
                top = module.split(".")[0]
                if not top.startswith("openroad_platform_"):
                    continue
                if any(top == allowed or module.startswith(allowed + ".")
                       for allowed in APP_ALLOWED_PLATFORM_IMPORTS):
                    continue
                out.append(Violation(
                    "G4", _rel(root, path), lineno,
                    f"app imports kernel internals via {module!r}; "
                    f"allowed: {', '.join(APP_ALLOWED_PLATFORM_IMPORTS)}",
                ))
    return out


# --------------------------------------------------------------------------
# G5 - an app must not open a kernel database
# --------------------------------------------------------------------------

_SQLITE_CONNECT_RE = re.compile(r"sqlite3\.connect\s*\(")
#: Kernel-owned database basenames.  An app that names one of these is reaching
#: past the typed API into the kernel's private state.
KERNEL_DB_NAMES = ("runtime.db", "agent-traces.db", "identity.db",
                   "provenance.db", "core.db")


def app_opens_kernel_db_violations(root: Path) -> list[Violation]:
    """G5: apps must not open a kernel-owned SQLite database."""
    out: list[Violation] = []
    for app in _app_dirs(root):
        for path in _walk_python(app):
            text = _read(path)
            lines = text.splitlines()
            for lineno, line in enumerate(lines, 1):
                code = line.split("#", 1)[0]
                if not _SQLITE_CONNECT_RE.search(code):
                    continue
                # Look at this statement plus the following two lines for the
                # database name, so a wrapped call is still caught.
                window = " ".join(
                    l.split("#", 1)[0] for l in lines[lineno - 1:lineno + 2]
                )
                for name in KERNEL_DB_NAMES:
                    if name in window:
                        out.append(Violation(
                            "G5", _rel(root, path), lineno,
                            f"app opens kernel database {name!r} directly",
                        ))
                        break
    return out


# --------------------------------------------------------------------------
# G6 - every app is an independently installable, independently runnable unit
# --------------------------------------------------------------------------

def app_packaging_violations(root: Path) -> list[Violation]:
    """G6: each app needs its own pyproject.toml and a process entry point."""
    out: list[Violation] = []
    for app in _app_dirs(root):
        rel = _rel(root, app)
        if not (app / "pyproject.toml").is_file():
            out.append(Violation("G6", rel, 0, "app has no pyproject.toml"))
        has_entry = (app / "__main__.py").is_file() or any(
            p.name in ("main.py", "server.py", "cli.py") for p in app.glob("*.py")
        ) or any(
            p.is_file() and not any(part.startswith(".") for part in p.parts)
            for p in app.rglob("__main__.py")
        )
        if not has_entry:
            out.append(Violation("G6", rel, 0, "app has no process entry point"))
    return out


# --------------------------------------------------------------------------
# G7 / G8 - the ratchet
# --------------------------------------------------------------------------

DEFAULT_MAX_FILE_LOC = 1500


def load_baseline(root: Path) -> dict:
    path = root / "guardrails" / "baseline.json"
    if not path.is_file():
        return {}
    return json.loads(_read(path))


def file_loc_ceiling_violations(root: Path) -> list[Violation]:
    """G7: no single source file may exceed the hard per-file ceiling."""
    baseline = load_baseline(root)
    ceiling = int(baseline.get("max_file_loc", DEFAULT_MAX_FILE_LOC))
    out: list[Violation] = []
    for path in _walk_python(root):
        n = _loc(path)
        if n > ceiling:
            out.append(Violation(
                "G7", _rel(root, path), 0,
                f"{n} lines exceeds the {ceiling}-line per-file ceiling",
            ))
    return out


def ratchet_violations(root: Path) -> list[Violation]:
    """G8: recorded sizes may only shrink.

    ``baseline.json`` holds ceilings.  A tree larger than a ceiling is a
    regression.  Raising a ceiling requires an approval file under
    ``approvals/``; without one this rule fails, which is what makes "we will
    clean it up later" impossible to say twice.
    """
    baseline = load_baseline(root)
    out: list[Violation] = []

    core_budget = baseline.get("core_total_loc")
    if core_budget is not None:
        # The budget covers shipped kernel code, not its tests.  Counting
        # tests would make the ceiling a moving target that grows every time
        # someone adds a case, which is the opposite of a ratchet.
        actual = sum(
            _loc(p) for d in KERNEL_DIRS
            for p in _walk_python(root / d)
            if (root / d).is_dir() and not _is_test_path(_rel(root, p))
        )
        if actual > int(core_budget):
            approved = _approved_ceiling(root, "core_total_loc")
            if approved is None or actual > approved:
                out.append(Violation(
                    "G8", CORE_DIR, 0,
                    f"kernel is {actual} lines, above the frozen budget "
                    f"{core_budget}; " + _approval_advice(approved),
                ))

    for rel, ceiling in (baseline.get("file_loc") or {}).items():
        path = root / rel
        if not path.is_file():
            continue
        actual = _loc(path)
        if actual > int(ceiling):
            approved = _approved_ceiling(root, rel)
            if approved is None or actual > approved:
                out.append(Violation(
                    "G8", rel, 0,
                    f"{actual} lines, above the frozen ceiling {ceiling}; "
                    + _approval_advice(approved),
                ))
    return out


def _approval_path(root: Path, key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return root / "approvals" / f"{safe}.md"


def _has_approval(root: Path, key: str) -> bool:
    """Whether a written reason exists for this key.

    Used where there is nothing to measure -- G10 asks "was this protected file
    changed on purpose", and the answer is the existence of the reason.
    """
    return _approval_path(root, key).is_file()


def _approved_ceiling(root: Path, key: str) -> int | None:
    """The size an approval authorises for *key*, or ``None``.

    The number, not the filename.  An approval that only had to *exist* stopped
    exempting nothing: once ``approvals/core_total_loc.md`` was written, every
    later comparison against the budget was skipped, so the kernel could grow
    past what was approved without a single gate objecting -- which it did.  A
    written reason is still required; it just has to say how much.

    The reason lives in ``approvals/<key>.md`` and the amount in
    ``approvals/ceiling.json``, so the document a human reads and the number the
    gate enforces cannot drift apart silently: a missing or unparsable grant is
    no approval at all.
    """
    if not _approval_path(root, key).is_file():
        return None
    grants_path = root / "approvals" / "ceiling.json"
    if not grants_path.is_file():
        return None
    try:
        grants = json.loads(_read(grants_path))
    except ValueError:
        return None
    if not isinstance(grants, dict):
        return None
    granted = grants.get(key)
    if isinstance(granted, bool) or not isinstance(granted, int):
        return None
    return granted


def _approval_advice(approved: int | None) -> str:
    if approved is None:
        return "lower the code or add an approval"
    return (f"the approval covers only {approved}; lower the code or raise "
            f"approvals/ceiling.json")


# --------------------------------------------------------------------------
# G9 - every app has a real end-to-end smoke
# --------------------------------------------------------------------------

def app_smoke_violations(root: Path) -> list[Violation]:
    """G9: each app must ship a real smoke entry point."""
    out: list[Violation] = []
    for app in _app_dirs(root):
        if not (app / "smoke.py").is_file():
            out.append(Violation("G9", _rel(root, app), 0,
                                 "app has no smoke.py end-to-end check"))
    return out


# --------------------------------------------------------------------------
# G10 - protected files are hash-locked
# --------------------------------------------------------------------------

def protected_hash_violations(root: Path) -> list[Violation]:
    """G10: protected components may not change without an approval file."""
    import hashlib

    baseline = load_baseline(root)
    out: list[Violation] = []
    for rel, expected in (baseline.get("protected_sha256") or {}).items():
        path = root / rel
        if not path.is_file():
            out.append(Violation("G10", rel, 0, "protected file is missing"))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected and not _has_approval(root, rel):
            out.append(Violation(
                "G10", rel, 0,
                "protected file changed; restore it or add an approval",
            ))
    return out


# --------------------------------------------------------------------------
# G11 - defensive anti-patterns (the Codex relapse gate)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AntiPattern:
    name: str
    regex: re.Pattern[str]
    why: str


ANTI_PATTERNS: tuple[AntiPattern, ...] = (
    AntiPattern(
        "sys-path-hack",
        re.compile(r"\bsys\.path\.(insert|append)\s*\("),
        "reaching across package boundaries by mutating sys.path",
    ),
    AntiPattern(
        "silent-except",
        re.compile(r"except\s+Exception\s*:\s*(pass|\.\.\.)\s*$"),
        "swallowing an exception hides a real failure",
    ),
    AntiPattern(
        "bare-except",
        re.compile(r"except\s*:\s*$"),
        "a bare except also catches KeyboardInterrupt and SystemExit",
    ),
    AntiPattern(
        "legacy-switch",
        re.compile(r"\b(use_legacy|legacy_mode|compat_mode|enable_legacy)\b"),
        "a compatibility switch keeps a dead path alive forever",
    ),
)

#: Historical-defence name segments.  A name like ``_legacy_projection`` is how a
#: removed design keeps being executed "just in case".
#:
#: Matching is by underscore-separated segment, not by substring: a substring
#: rule flags ``flow_compatibility.json`` -- a real artifact name -- and a gate
#: that cries wolf gets switched off.
LEGACY_NAME_SEGMENTS = frozenset({
    "legacy", "compat", "deprecated", "obsolete",
})
#:
#: ``fallback`` and ``old`` are deliberately absent.  A fallback *value* and
#: an old value in a swap are ordinary code; only a fallback *path* is the
#: anti-pattern, and no identifier-level rule can tell those apart.  v1's
#: actual offenders all used "legacy" (include_legacy 308, legacy 97,
#: legacy_root 74), so the narrower list loses almost no detection and
#: removes the false positives that would get the gate switched off.


def legacy_name_hits(code: str) -> list[str]:
    """Identifiers whose underscore-separated segments name a dead path."""
    hits = []
    for match in _IDENTIFIER_RE.finditer(code):
        identifier = match.group(0)
        segments = [seg for seg in identifier.lower().split("_") if seg]
        if any(seg in LEGACY_NAME_SEGMENTS for seg in segments):
            hits.append(identifier)
    return hits

#: Files allowed to contain an anti-pattern, with the reason it is unavoidable.
ANTIPATTERN_FILE_EXEMPTIONS: dict[str, str] = {}


def defensive_antipattern_violations(root: Path) -> list[Violation]:
    """G11: forbid the defensive patterns that let dead structure survive."""
    out: list[Violation] = []
    for path in _walk_python(root):
        rel = _rel(root, path)
        if rel in ANTIPATTERN_FILE_EXEMPTIONS:
            continue
        text = _read(path)
        try:
            prose_lines = _docstring_lines(ast.parse(text, filename=str(path)))
        except SyntaxError:
            prose_lines = set()
        for lineno, line in enumerate(text.splitlines(), 1):
            # Prose explaining an anti-pattern is not the anti-pattern.  A
            # docstring that says "the old runtime did X" is documentation,
            # not a retained dead path.
            if lineno in prose_lines:
                continue
            code = line.split("#", 1)[0].rstrip()
            if not code.strip():
                continue
            for pattern in ANTI_PATTERNS:
                if pattern.regex.search(code):
                    out.append(Violation(
                        "G11", rel, lineno,
                        f"{pattern.name}: {pattern.why}",
                    ))
            if not _is_quarantine_path(rel):
                for identifier in legacy_name_hits(code):
                    out.append(Violation(
                        "G11", rel, lineno,
                        f"identifier {identifier!r} names a dead path; "
                        f"delete the old path or raise the conflict",
                    ))
    return out


def _is_quarantine_path(rel: str) -> bool:
    """Archived v1 material is exempt; it is explicitly not live code."""
    return rel.startswith("archive/") or rel.startswith("v1/")


# --------------------------------------------------------------------------
# G12 - unreachable code
# --------------------------------------------------------------------------

_TERMINAL = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def unreachable_code_violations(root: Path) -> list[Violation]:
    """G12: statements after a terminal statement can never run.

    v1 shipped 100+ lines of an optimizer behind an unconditional ``raise``.
    Nothing caught it because nothing looked.
    """
    out: list[Violation] = []
    for path in _walk_python(root):
        try:
            tree = ast.parse(_read(path), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for index, stmt in enumerate(body[:-1]):
                if isinstance(stmt, _TERMINAL):
                    nxt = body[index + 1]
                    out.append(Violation(
                        "G12", _rel(root, path), getattr(nxt, "lineno", 0),
                        f"unreachable {type(nxt).__name__} after "
                        f"{type(stmt).__name__}",
                    ))
    return out


# --------------------------------------------------------------------------
# G13 - one implementation per concern
# --------------------------------------------------------------------------

#: A concern may be implemented exactly once in the tree.  This is the rule
#: that stops "reimplement it here instead of reusing it there".
#:
#: The digest pattern excludes validators: ``validate_sha256`` checks a string
#: against a format, it does not compute a digest, so counting it as a second
#: implementation would be a false positive.  It still catches every real
#: duplicate the v1 tree contained.
@dataclass(frozen=True)
class Concern:
    """One thing the platform implements once.

    ``per_file`` distinguishes "one implementation" from "one implementation
    site".  A dispatcher is one thing that happens to need a method per HTTP
    verb -- ``do_GET`` and ``do_POST`` in one class are two halves of one
    dispatcher, not two dispatchers.  A digest, by contrast, is one function;
    two of them in one file is still duplication.
    """

    pattern: re.Pattern[str]
    per_file: bool = False


SINGLETON_CONCERNS: dict[str, Concern] = {
    "sha256-digest": Concern(re.compile(
        r"def\s+(?!(?:validate|check|assert|require|is)_)\w*sha256\w*\s*\("
    )),
    "json-response-envelope": Concern(
        re.compile(r"def\s+\w*(json_response|_json|respond|reply)\s*\(")
    ),
    "route-dispatcher": Concern(
        re.compile(r"def\s+do_(GET|POST)\s*\("), per_file=True
    ),
}


def _is_test_path(rel: str) -> bool:
    """True when a file is test scaffolding rather than shipped code.

    ``smoke.py`` counts because G9 defines it as an app's end-to-end check:
    a smoke that stands up a stub and drives it is test code, and its stub
    must not be mistaken for a second implementation of a platform concern.
    """
    parts = rel.split("/")
    if "tests" in parts or "fixtures" in parts:
        return True
    return parts[-1] == "smoke.py"


def _concern_zone(rel: str) -> str:
    """Which independently-authored unit a file belongs to.

    A plugin and an app are each separate programs.  A plugin speaks the JSON
    adapter protocol and may be written in any language; an app is its own
    process with its own database and its own HTTP surface.  Neither may be
    forced to import the platform's code, so each is allowed its own digest
    and its own request dispatcher.  Neither is allowed two, which is why the
    rule is applied per zone rather than to the tree as a whole.
    """
    parts = rel.split("/")
    if parts[0] == "plugins" and len(parts) > 1:
        return f"plugin:{parts[1]}"
    if parts[0] == "apps" and len(parts) > 1:
        return f"app:{parts[1]}"
    return "platform"


def duplicate_implementation_violations(root: Path) -> list[Violation]:
    """G13: within one independently-authored unit, a concern is implemented once.

    Definitions are located through the AST, not by scanning raw text.  A code
    generator legitimately contains sample definitions inside string literals;
    counting those would make the rule unusable and tempt someone to disable it.
    """
    out: list[Violation] = []
    for concern, spec in SINGLETON_CONCERNS.items():
        rx = spec.pattern
        hits_by_zone: dict[str, list[tuple[str, int]]] = {}
        for path in _walk_python(root):
            rel = _rel(root, path)
            # A test double is not a second implementation of a platform
            # concern.  A fake app must define do_GET because the HTTP server
            # requires it; counting that would make the rule unusable.
            if _is_test_path(rel):
                continue
            try:
                tree = ast.parse(_read(path), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if rx.search(f"def {node.name}("):
                    hits_by_zone.setdefault(_concern_zone(rel), []).append(
                        (rel, node.lineno)
                    )
                    if spec.per_file:
                        # One site per file is enough: the remaining methods
                        # of the same dispatcher are the same concern.
                        break
        for zone, hits in hits_by_zone.items():
            if len(hits) < 2:
                continue
            first = hits[0]
            for rel, lineno in hits[1:]:
                out.append(Violation(
                    "G13", rel, lineno,
                    f"{concern!r} already implemented at {first[0]}:{first[1]} "
                    f"within {zone}; reuse it instead of writing a second one",
                ))
    return out


# --------------------------------------------------------------------------
# G14 - change budget
# --------------------------------------------------------------------------

MAX_LAYERS_PER_CHANGE = 1


def _layer_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] in ("core", "gateway"):
        return "kernel"
    if parts[0] == "apps" and len(parts) > 1:
        return f"app:{parts[1]}"
    if parts[0] == "plugins" and len(parts) > 1:
        return f"plugin:{parts[1]}"
    if parts[0] == "guardrails":
        return "guardrails"
    return parts[0]


def change_budget_violations(root: Path, changed_files: list[str]) -> list[Violation]:
    """G14: one change may touch one architectural layer.

    ``changed_files`` is supplied by the CI step from ``git diff``.
    """
    layers: dict[str, list[str]] = {}
    for rel in changed_files:
        if rel.startswith("docs/") or rel.startswith("guardrails/"):
            continue
        layers.setdefault(_layer_of(rel), []).append(rel)
    if len(layers) <= MAX_LAYERS_PER_CHANGE:
        return []
    summary = "; ".join(f"{k} ({len(v)} files)" for k, v in sorted(layers.items()))
    return [Violation(
        "G14", ", ".join(sorted(layers)), 0,
        f"this change spans {len(layers)} layers: {summary}. "
        f"A change must touch one layer only.",
    )]


# --------------------------------------------------------------------------
# G16 - the kernel must not import an app or a plugin
# --------------------------------------------------------------------------

PLUGIN_DIR = "plugins"
APP_PACKAGE_PREFIX = "openroad_app_"


def _plugin_dirs(root: Path) -> list[Path]:
    base = root / PLUGIN_DIR
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def kernel_imports_capability_violations(root: Path) -> list[Violation]:
    """G16: the kernel may not import an application or a plugin.

    This is the property that makes the platform thin, and until now nothing
    enforced it.  It was true -- measured at zero -- but only by convention, and
    a single convenient import would end it silently: no other rule in this file
    would notice, because G1 catches a plugin *name* appearing in kernel text,
    not the kernel importing the plugin's code.

    The previous platform failed here first.  Its worker imported the entire
    application layer to run a command, so "remove the capability and the
    platform still works" was false, and every capability it supported was an
    implicit part of the kernel.

    Three shapes are refused: an import of a plugin directory by name, an import
    through the ``plugins.`` path, and an import of an application package.
    """
    out: list[Violation] = []
    plugin_names = {d.name for d in _plugin_dirs(root)}
    for area in KERNEL_DIRS:
        base = root / area
        if not base.is_dir():
            continue
        for path in _walk_python(base):
            for module, lineno in _imported_modules(path):
                parts = module.split(".")
                where = _rel(root, path)
                if parts[0] == APP_DIR or parts[0].startswith(APP_PACKAGE_PREFIX):
                    out.append(Violation(
                        "G16", where, lineno,
                        f"the kernel imports the application {module!r}",
                    ))
                elif PLUGIN_DIR in parts:
                    out.append(Violation(
                        "G16", where, lineno,
                        f"the kernel imports plugin code {module!r}",
                    ))
                elif parts[0] in plugin_names:
                    out.append(Violation(
                        "G16", where, lineno,
                        f"the kernel imports the plugin {parts[0]!r}",
                    ))
    return out


# --------------------------------------------------------------------------
# G17 - the kernel's packages form a directed acyclic graph
# --------------------------------------------------------------------------

KERNEL_PACKAGE_PREFIX = "openroad_platform_"


def _kernel_packages(root: Path) -> dict[str, Path]:
    """Package name -> its directory, for every kernel package in the tree."""
    found: dict[str, Path] = {}
    for area in KERNEL_DIRS:
        base = root / area
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("__init__.py")):
            name = path.parent.name
            if name.startswith(KERNEL_PACKAGE_PREFIX):
                found[name] = path.parent
    return found


def _package_edges(root: Path, packages: dict[str, Path]) -> dict[str, set[str]]:
    """Which kernel packages each one imports, at any depth.

    Imports inside functions count.  The cycle this gate was written for was
    hidden behind exactly that: a function-local import in `worker.py` reached
    the registry and the evaluator, while the module header showed neither.
    """
    edges: dict[str, set[str]] = {name: set() for name in packages}
    for name, directory in packages.items():
        for path in _walk_python(directory):
            for module, _ in _imported_modules(path):
                target = module.split(".")[0]
                if target in packages and target != name:
                    edges[name].add(target)
    return edges


def _find_cycle(edges: dict[str, set[str]]) -> list[str] | None:
    """One cycle as a path, or None if the graph is acyclic."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {name: WHITE for name in edges}
    for start in sorted(edges):
        if colour[start] != WHITE:
            continue
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        while stack:
            node, path = stack[-1]
            colour[node] = GREY
            unvisited = sorted(t for t in edges[node] if colour[t] != BLACK)
            advanced = False
            for target in unvisited:
                if colour[target] == GREY:
                    return path[path.index(target):] + [target]
                stack.append((target, path + [target]))
                advanced = True
                break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
    return None


def kernel_package_cycle_violations(root: Path) -> list[Violation]:
    """G17: no two kernel packages may import each other.

    A cycle is how a layered design quietly becomes one lump.  Two packages that
    import each other cannot be tested alone, released alone, or reasoned about
    one at a time, and the import graph stops telling a reader which way the
    dependency goes.

    It is also invisible to every other gate here.  The cycle this rule was
    written for -- the runtime importing the evaluator while the evaluator
    imported the runtime -- was hidden behind an import inside a function, so it
    appeared in no module header, broke no test, and was found by accident.
    """
    packages = _kernel_packages(root)
    cycle = _find_cycle(_package_edges(root, packages))
    if cycle is None:
        return []
    return [Violation(
        "G17", _rel(root, packages[cycle[0]]), 0,
        "kernel packages import each other in a cycle: " + " -> ".join(cycle),
    )]


# --------------------------------------------------------------------------
# G15 - every declared rule has a gate, and every gate can fail
# --------------------------------------------------------------------------

RULE_ID_RE = re.compile(r"^\|\s*(G\d+)\s*\|", re.M)


def declared_rule_ids(root: Path) -> list[str]:
    """Rule ids declared in the AGENTS.md rule table."""
    agents = root / "AGENTS.md"
    if not agents.is_file():
        return []
    return sorted(set(RULE_ID_RE.findall(_read(agents))))


def rule_coverage_violations(root: Path) -> list[Violation]:
    """G15: each declared rule must have a gate module and a negative fixture."""
    out: list[Violation] = []
    gates = root / "guardrails"
    for rule in declared_rule_ids(root):
        lowered = rule.lower()
        has_gate = any(
            p.name.startswith(f"test_{lowered}_") for p in gates.glob("test_*.py")
        )
        if not has_gate:
            out.append(Violation(
                "G15", "AGENTS.md", 0,
                f"rule {rule} is declared but no guardrails/test_{lowered}_*.py exists",
            ))
        if not (gates / "negative" / rule).is_dir():
            out.append(Violation(
                "G15", "AGENTS.md", 0,
                f"rule {rule} has no negative fixture, so it is unproven",
            ))
    return out


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

#: rule id -> (function, needs negative fixture)
RULES: dict[str, object] = {
    "G1": kernel_plugin_name_violations,
    "G2": kernel_adapter_file_violations,
    "G3": app_cross_import_violations,
    "G4": app_forbidden_core_import_violations,
    "G5": app_opens_kernel_db_violations,
    "G6": app_packaging_violations,
    "G7": file_loc_ceiling_violations,
    "G8": ratchet_violations,
    "G9": app_smoke_violations,
    "G10": protected_hash_violations,
    "G11": defensive_antipattern_violations,
    "G12": unreachable_code_violations,
    "G13": duplicate_implementation_violations,
    "G15": rule_coverage_violations,
    "G16": kernel_imports_capability_violations,
    "G17": kernel_package_cycle_violations,
}

#: Rules that operate on a supplied list rather than a static tree scan.
CHANGE_RULES = {"G14": change_budget_violations}


def scan(root: Path, rule: str) -> list[Violation]:
    fn = RULES[rule]
    return fn(Path(root))  # type: ignore[operator]
