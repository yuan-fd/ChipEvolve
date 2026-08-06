# DPLEvolve license and execution boundary

- Source: `CODA-Team/DPLEvolve` at commit
  `96d8c613d62bf3431083bb5e52c7df8853d5a622`.
- License: BSD-3-Clause; the cached `LICENSE` digest is recorded in
  `source.lock.json`.
- The platform does not vendor the private upstream source. The ignored cache
  at `.external-src/dplevolve` is bound by a deterministic content digest.
- Plugin v1 runs only the upstream static release-readiness gate in an
  isolated staged mirror. It cannot run EDA, mutate the source cache, submit a
  candidate, or promote a patch.
- Candidate source evaluation is separately constrained to
  `tools/OpenROAD/src/dpl_evolve/`; baseline/evaluator/flow/classic DPL and
  neighboring OpenROAD modules remain protected. Promotion is evidence-only
  until a human explicitly approves a manual action.
