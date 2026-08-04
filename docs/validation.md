# Validation record

Validated on 2026-07-24 with:

- Python 3.9.9;
- OpenROAD `26Q1-1961-g63ed2e0fe5`;
- Yosys `0.63`;
- ORFS commit `51ad1231a`;
- Nangate45 and the prototype `test_counter.v` input.

## Automated tests

The original P0 `python3 -m pytest -q` baseline passed 22 tests. Coverage includes contracts, queue
reopen/claim/cancel, silent-process timeout, process-group cleanup, simulated
six-stage ORFS execution, artifact hard gates, recoverable GDS export, metric
parsing, density data, netlist analysis, and dependency-free circuit overview.

## Native smoke runs

The direct runner completed real ORFS synthesis in 8.521 seconds and recorded a
non-empty `1_synth.odb` with its SHA-256 digest.

The durable queue and independent worker then executed all six real stages.
Synthesis through route passed. The final PSM report failed with
`PSM-0069` because the prototype's simplified small-design PDN has unconnected
VDD shapes. The platform correctly reported:

- `synthesizable=true`;
- `functionally_verified=false`;
- `implementation_valid=false`.

The separate ORFS `gds` recovery target completed successfully from the final
DEF. It produced a 53,860-byte GDS with SHA-256
`fb9052855292d48fe8535af762106b351ba6b28efba62e4a95a44990ab9588c0`.
This proves the GDS path without hiding the failed implementation gate.

Generated smoke-run workspaces are removed after validation; they are runtime
artifacts and are excluded by `.gitignore`.

## P0 evidence re-audit (2026-08-04)

The persisted development database contains six jobs: two succeeded and four
failed. One succeeded job completed all six stages through `finish` and records
both `implementation_valid=true` and `gds_complete=true`.

P0 recomputed the size and SHA-256 of every artifact referenced by the six
stored results: 45 matched, zero were missing, and zero differed. The older
finish-failure/GDS-recovery evidence remains valid and continues to report a
failed implementation rather than hiding the failed gate.

These records are historical evidence, not a substitute for the new Runtime
and ORFS-plugin acceptance run required in P2.

## P2 ORFS-plugin acceptance (2026-08-04)

P2 completed the previously required new-Runtime acceptance. `orfs@1.0.0`
executed a real Nangate45 `mux_2to1` run through synth, floorplan, place, CTS,
route, and finish in 77.05 seconds. The v1 state contains one successful Run,
StageRun, and Attempt, plus 17 artifacts, 7 metrics, and 29 events.

The final GDS is 19,572 bytes with SHA-256
`d20ee44ef216af20a896b4a48794d2ee3fdd8de70b7fe8280fb8ae13a59ad1e6`.
Both `implementation_valid` and `gds_complete` are true. Full tests now pass
53 cases, including input-tamper rejection and nested-process cancellation.
See `docs/evidence/P2_ORFS_ACCEPTANCE.md` for the evidence table and protection
audit.
