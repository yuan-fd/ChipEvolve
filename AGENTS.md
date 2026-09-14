# Rules for this repository

This repository is a **thin control plane for external research capabilities**.
The platform owns trust. It does not own algorithms.

Read this file completely before writing code. Every rule below is enforced by a
test in `guardrails/`. A rule you cannot satisfy is a design problem to raise,
not a rule to work around.

## The four layers

```
gateway/    entry only: identity, routing, navigation. No domain logic.
apps/       one process each, one database each, one UI each. Siblings never import.
contracts/  the shared language. Dependency-free.
core/       the kernel: runtime, evaluator, registry, provenance, identity.
plugins/    external algorithms. Each owns its own adapter.
```

## Rules

| Rule | Meaning | Enforced by |
| --- | --- | --- |
| G1 | The kernel must not name a concrete plugin, tool, or vendor. | `guardrails/test_g1_no_concrete_plugin_names_in_kernel.py` |
| G2 | No `*_plugin.py` or `*_adapter.py` may live in the kernel. | `guardrails/test_g2_no_adapters_in_kernel.py` |
| G3 | One app must never import a sibling app. | `guardrails/test_g3_apps_do_not_import_each_other.py` |
| G4 | An app reaches the platform only through `contracts` and the core client. | `guardrails/test_g4_apps_only_import_contracts_and_client.py` |
| G5 | An app must never open a kernel-owned database. | `guardrails/test_g5_apps_do_not_open_kernel_databases.py` |
| G6 | Each app has its own `pyproject.toml` and its own process entry point. | `guardrails/test_g6_apps_are_independently_installable.py` |
| G7 | No source file exceeds the per-file line ceiling. | `guardrails/test_g7_file_size_ceiling.py` |
| G8 | Recorded sizes may only shrink. Growth requires an approval naming the size. | `guardrails/test_g8_ratchet_only_shrinks.py` |
| G9 | Each app ships a real end-to-end smoke. | `guardrails/test_g9_apps_have_end_to_end_smoke.py` |
| G10 | Protected components are hash-locked. | `guardrails/test_g10_protected_files_are_hash_locked.py` |
| G11 | Defensive anti-patterns are forbidden. | `guardrails/test_g11_no_defensive_antipatterns.py` |
| G12 | No unreachable code. | `guardrails/test_g12_no_unreachable_code.py` |
| G13 | One implementation per concern. Reuse before you write. | `guardrails/test_g13_one_implementation_per_concern.py` |
| G14 | One change touches one layer. | `guardrails/test_g14_change_budget.py` |
| G15 | Every rule here has a gate and a negative fixture. | `guardrails/test_g15_declared_rules_have_gates.py` |
| G16 | The kernel must not import an app or a plugin. | `guardrails/test_g16_kernel_does_not_import_capabilities.py` |

## What "defensive" means here

There are two kinds of defensive programming. Only one is allowed.

**Allowed — boundary defence.** Validate input where it enters the system.
Raise a specific, named error. Fail loudly and immediately.

**Forbidden — historical defence.** Every one of these has cost this project
real time:

- keeping a dead path alive "just in case" (G12)
- a `legacy` / `compat` / `fallback` branch that preserves a removed design (G11)
- swallowing an exception so a broken path looks healthy (G11)
- copying an implementation instead of importing the existing one (G13)
- mutating `sys.path` to cross a package boundary (G11)
- wrapping a new name around an old monolith instead of removing it

If you are about to write a compatibility shim, stop. Either delete the old
path or raise the conflict. Do not maintain both.

## How to add a capability

New capability goes in `plugins/<name>/` with its own manifest and its own
adapter. It does not go in the kernel, and it does not go in an app.

If adding a capability requires editing the kernel, the kernel is missing a
contract. Add the contract to `contracts/` first.

## How to add an app

```
python3 tools/new_app.py <name>
```

That creates a compliant skeleton. An app created by hand will fail G6 and G9.

## When you are stuck

Stop and write it down. State the problem, the evidence, why the current plan
fails, two options, and a recommendation. Do not weaken a gate to make a test
pass, and do not add a compatibility layer to avoid a decision.

## Amending the rules

`guardrails/baseline.json` holds the size ceilings. Raising a ceiling requires
two things: a reason in `approvals/<key>.md` and the amount in
`approvals/ceiling.json`. Both, because an approval that only had to *exist*
exempted its component for the rest of the project's life -- the kernel sat above
its budget with every gate green. Removing a forbidden token from `rules.py` is
never allowed: that silently re-opens a hole.
