# Validation record

Validated on 2026-07-24 with:

- Python 3.9.9;
- OpenROAD `26Q1-1961-g63ed2e0fe5`;
- Yosys `0.63`;
- ORFS commit `51ad1231a`;
- Nangate45 and the prototype `test_counter.v` input.

## Automated tests

`python3 -m pytest -q` passes 15 tests. Coverage includes contracts, queue
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

