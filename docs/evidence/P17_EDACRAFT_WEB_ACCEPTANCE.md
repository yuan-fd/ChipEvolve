# P17 EDACraft and web acceptance

Date: 2026-08-06
Pinned EDACraft commit: `739eee0f3ced8fc3cbb6f01b6cc89414758fd898`

## Runtime evidence

The replay command was:

```bash
python3 scripts/run_p17_acceptance.py \
  --output-root .tools/p17-acceptance/runtime-20260806
```

All six independent Runtime runs succeeded:

| Component | Run | Evidence |
|---|---|---|
| RTLCraft | `f637c8cecb7e44489c6720fec25f90aa` | real upstream DSL emitted 646-byte `PlatformAccumulator.sv` |
| EDACode | `7f2aefe773584e6ab4dd074b72e238af` | five security-relevant source surfaces audited; no tools executed |
| TCADCraft | `bb0fff0fc84f4a5da7f3a3576ad5f092` | real upstream `Box` geometry evaluated for two points |
| MoMCraft | `e44f3a59f458465b834435c84361b0f5` | real upstream Touchstone writer/reader round trip |
| CktCraft | `238782625b7b434c94d21fb6427b9cd1` | v0.2 CLI/build/divider-netlist surface admitted |
| ImplCraft | `d1fc443767c74254bd6fd7ff2eea62a0` | preserved adapter generated three Tcl scripts plus state/report |

The ignored replay directory contains `runtime.db`, all attempt workspaces,
`acceptance.json`, and complete artifact hashes. Representative SHA-256 values:

- RTLCraft RTL: `c30868b0a96b28799d9fb97ad7de280e45284751f1846806290fd967d05cb118`
- TCADCraft geometry: `3273c01eb485edd6d8271e17638d8c68d8c7b2d4d3f214be4b4a54c27ff5e226`
- MoMCraft `.s2p`: `8681dded3ce55de850a9953fd9c4c8c56c6ee6dd01685ce843d32aae55d03cae`
- ImplCraft floorplan Tcl: `eaa0bc6a178614a467725f36d166b161aa7356b7ccf62a8daaa1fa1fcaf9f170`

## Web and API gates

- exactly five top-level English tabs are present;
- all project counts/results/learning content come from API/store projections;
- Graphviz and KLayout artifact paths remain the visualization sources;
- EDACraft catalog contains exactly six unique plugin identities;
- Node syntax and Python compilation pass;
- static assets and new API endpoints return successful HTTP responses.

## Non-claims

This acceptance did not run full TCAD convergence, a Method-of-Moments solve,
CktCraft numerical analyses, an EDACode LLM, commercial EDA, or an expensive
DPLEvolve flow. Those capability levels remain explicit future promotions.
