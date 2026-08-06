# EDACraft license audit

The pinned repository root uses an MIT-like license with an additional
non-commercial restriction. It must not be described as standard MIT or as
unrestricted commercial software. Individual subprojects may include their own
license files; the platform uses the stricter root-level distribution boundary
for the extension pack.

The upstream source remains in the ignored `.external-src/edacraft` cache and
is not vendored into this repository. Platform manifests, adapters, and test
evidence do not relicense upstream code.

CktCraft v0.2 uses generated/static C++ device models. Historical OSDI/OpenVAF
DLL wording in the monorepo root is not used as the platform's capability
claim; fixed component source and its subproject README are authoritative.
