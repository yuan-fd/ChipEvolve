# Current status

updated_at: 2026-08-25
phase: v2 engineering acceptance and report freeze

## Product boundary

- The v2 2D product exposes one execution path: `POST /api/v2/closed-loops`
  followed by server-owned `run-to-boundary` execution.
- Baseline is round 0 inside that loop. Sequential scan, grid/manual tuning,
  standalone baseline, recommendation approval, and manual campaign modes are
  not product routes. Seed, repetition count, search bounds, transition count,
  stall rule, and clock-search controls are rejected at the HTTP boundary.
  The legacy read-only `/api/optimization/studies` surface is also deleted;
  offline stores and comparison policies exist only for reproducible research
  and cannot be mistaken for a second user workflow.
- The browser never accepts a Provider profile or API key. Natural-language
  SpecIR, Verification Agent, and RTLScout use the platform-managed
  `gpt-5.6-terra` Codex service. External RTLScout credential providers were
  deleted from the execution plugin; `fake` is an isolated test fixture only.

## Implemented v2 chain

`natural language -> SpecIR -> independent Verification Agent -> frozen
testbench -> RTLScout candidate iteration -> lint/simulation/mutation -> ORFS
baseline -> repeated coupled-parameter BO/GP -> three-stall diagnosis ->
evidence/hypothesis/holdout memory`

Workflow Runtime is the sole process and artifact authority. Models propose
structured specifications, tests, RTL candidates, diagnoses, or hypotheses;
they cannot register their own PPA numbers or bypass the Runtime.

## Real evidence snapshot

- RTL fixed suite: four natural-language designs (gcd, FIFO, UART TX and the
  small `ibex_alu` block), one generation seed each, all reached registered GDS.
  Evidence: `artifacts/v2-real-rtl-suite-20260825/aggregate.json`.
- Multi-design loop: 48 full-flow runs, 3/4 designs reached the preregistered
  0.5% practical utility threshold.
  Evidence: `artifacts/v2-multidesign-closed-loop-20260825/aggregate.json`.
- BO vs seeded random: 144 full-flow runs per policy. Threshold events were
  7/12 for BO and 4/12 for random; per-design median winners were 2:2. This is
  descriptive evidence, not statistical or universal superiority.
  Evidence: `artifacts/v2-parameter-ablation-multiseed-20260825/aggregate.json`.
- Causal holdout: 24/24 full-flow runs. The GCD interaction did not replicate
  on FIFO, so the platform recorded `refuted` and blocked action eligibility.
  Evidence: `artifacts/v2-learning-ablation-20260825/aggregate.json`.
- EDAIR: four real designs expose timing paths, logical and physical objects,
  raw artifact hashes, loss manifests, and bounded Agent packets instead of a
  KPI-only summary. Evidence: `artifacts/v2-edair-ablation-20260825/aggregate.json`.
- Agent architecture: all four real traces contain the stable eight-phase
  protocol; interruption/resume and permission behavior are separately tested.
  Evidence: `artifacts/v2-agent-architecture-20260825/aggregate.json`.

## Claim boundaries

- The RTL experiment is a four-design, one-seed feasibility suite, not proof of
  arbitrary-spec generalization or a complete Ibex core.
- The parameter ablation is too small for a statistical-significance claim.
- The learning experiment blocked one observed false transfer; it is not a
  population estimate or universal causal law.
- EDAIR fidelity and eight-phase traces do not by themselves prove QoR gains.
- v3 repair executors (TimingECO, Resynth, EvoDRC and source evolution) remain
  outside this v2 goal.

The exact protocols and artifact-level boundaries are maintained in
`docs/V2_RESEARCH_ACCEPTANCE.md`. Historical P0-P22 evidence files describe
past prototypes and must not be used as the current product menu.
