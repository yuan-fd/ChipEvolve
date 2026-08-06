# P19–P22 platform acceptance

P19 maps four cited research methods to concrete platform implementations:
multi-objective BO/Pareto planning, context-filtered evidence RAG, offline RL
shadow advice, and GP surrogate calibration. The catalog records DOI, role,
implementation symbols, maturity, and the Workflow Runtime boundary.

P20 adds deterministic bounded Latin-hypercube sampling, leave-one-out GP error,
empirical interval coverage, residual scale, and OOD checks over configured
bounds, normalized nearest-sample distance, and predictive uncertainty. Generated
benchmark points and predictions remain data; they are never admitted as observed
Runtime outcomes.

P21 provides a two-step user gate. Accepting or modifying a recommendation creates
an idempotent one-candidate Campaign with a decision fingerprint. A separate
confirmation submits it to Runtime. Terminal evidence is then quarantined,
verified, admitted to the tenant store, and appended to the matching study as an
observed sample. Rejection starts nothing.

P22 exposes citations, confidence, OOD state, approval, and submission in the
five-page site. It adds a four-flow demo manifest and an integrity-checked SQLite
backup/restore command. Nine current state databases were backed up with SQLite's
online backup API and restored into a new directory without overwriting the live
state.

Full regression: 187 passed, 2 skipped, 0 failed. The two skips are optional
`pya`-module visualization tests in the isolated Python environment; existing
KLayout-generated evidence is unchanged. Node syntax, Python compilation, JSON,
diff whitespace, credential-pattern scan, source-lock checks, and the real P18
Runtime replay passed.
