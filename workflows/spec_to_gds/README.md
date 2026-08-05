# Spec to GDS workflow

P12 implements the bounded local control loop:

multi-turn specification -> structured proposal -> optional RTL candidate ->
deterministic validation -> explicit confirmation -> authoritative Runtime ->
ORFS stages -> hashed GDS and visual evidence.

`CodexCliSpecProvider` supports only allowlisted Terra/Sol models and runs in an
ephemeral read-only empty directory. The deterministic Provider remains usable
offline. Provider output is data, never a command. Sessions enforce turn, call,
EDA-run, repair and wall-clock budgets; Runtime submission requires confirmation
and is idempotent by stable task id.

The real P12 acceptance produced GDS after an evidence-bounded floorplan-area
repair. Synthesis success is still not general functional verification: designs
that require reference models, simulation or formal proof must attach those
checks before `functionally_verified` can be true.
