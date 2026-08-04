# Migration plan

## Baseline completed in this repository

- v1 run contracts;
- durable development queue;
- independent worker and cancellation polling;
- process-group guardian;
- native ORFS runner with hard artifact gates;
- analysis, netlist, heatmap, layout, and schematic libraries;
- unit tests plus native ORFS smoke validation.

## Next sequence

1. Review contracts and commit this clean baseline.
2. Add project/design tables and artifact import instead of moving 300+ MB of
   prototype output into Git.
3. Add FastAPI endpoints over the scheduler and artifact allowlist.
4. Migrate the two UI workspaces component by component.
5. Add executable specification, simulation, and formal validation contracts.
6. Integrate RTLScout, then one optimizer adapter, through contract tests.

