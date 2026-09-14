# Approval: `core/registry/src/openroad_platform_registry/validate.py` 0 -> 247

## What this is

The plugin conformance checker, as a module of the package whose rules it
applies.  Exposed as `openroad-platform-plugin-validate`.

## Why it is kernel and not a script under `tools/`

It was written as `tools/plugin_validate.py` first, and two things were wrong
with that.

It mutated `sys.path` to reach the contract and the registry, which G11 flagged
immediately -- and G11 was right: reaching across a package boundary by editing
the import path is the anti-pattern this repository forbids by name.  The fix is
not a cleverer path; it is for the tool to live where its imports are legitimate.

And it applied the registry's own rules from outside the registry: what a
manifest must declare, what an admission record must say, when an entrypoint
escapes its directory.  A checker that restates those rules is a second set of
rules, free to disagree with the first.  As a module of the registry it imports
them, so drift is impossible.

## What it deliberately does not do

It reports; it does not admit.  Whether a plugin executes here is the platform's
decision, recorded in `admissions/`, and a command-line tool cannot make it.  It
also checks shape rather than existence for an absolute entrypoint, because such
a path may exist only in the deployment environment, and a validator that rejects
what the platform accepts is a validator that lies.

## The first draft was wrong in exactly that way

It refused a manifest that listed a platform-reserved artifact kind -- which the
protected evaluator must do, or its own verdict artifact would fail its own
allowlist.  The tool was stricter than the platform.  Writing the checker found
the bug in the checker, which is the useful direction for that to happen.
