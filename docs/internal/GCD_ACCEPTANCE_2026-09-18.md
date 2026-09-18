# GCD acceptance record — 2026-09-18

## Result

The real ORFS GCD flow passed through the platform public API:

```text
1 passed in 157.53s
```

Command:

```bash
cd /share/home/yuanwenjie/agenticeda-orfs && \
AGENTICEDA_PLATFORM=/share/home/yuanwenjie/openroad-platform-v2 \
OPENROAD_PLATFORM_ORFS_ROOT=/share/home/yuanwenjie/OpenROAD-flow-scripts \
OPENROAD_PLATFORM_OPENROAD_BIN=/share/home/yuanwenjie/bin/openroad \
OPENROAD_PLATFORM_YOSYS_BIN=/share/home/yuanwenjie/bin/yosys \
OPENROAD_PLATFORM_KLAYOUT_BIN=/share/home/yuanwenjie/bin/klayout \
python3 -m pytest -q tests/test_against_platform.py::test_public_api_runs_staged_gcd_to_finish -vv
```

The test submits `gcd` with `target_stage=finish`, stages the RTL through the
platform, runs the external `orfs` plugin through a worker, and reads the
result through the platform client. It does not call the EDA tools directly
from the assertion code.

## Toolchain evidence

- OpenROAD: `26Q1-1961-g63ed2e0fe5`
- Yosys: `0.63`, git `d3e297fcd`
- KLayout: `0.30.6`
- ORFS checkout: `51ad1231a231ee85234c06db807688d029b85c35`
- ORFS plugin provenance revision: `e7a0725758c0abde2b69e2cdba783435238748ef`
- The OpenROAD launcher supplies GCC 12 and shared dependency directories from the host installation.

## Verified platform evidence

- Terminal run status is `succeeded`.
- The attempt workspace is below the platform state root.
- The staged RTL has a recorded destination and SHA-256 digest.
- Platform artifacts include GDS, ODB, DEF, netlist, report and input manifest.
- A toolchain snapshot is registered as an environment artifact.
- Metrics are complete and reference artifact hashes.
- Timeline events contain `prepare`, `synth`, `floorplan`, `place`, `cts`, `route` and `finish`.
- The acceptance cleanup left no ORFS, OpenROAD, Yosys or KLayout process running.

This proves execution success and evidence collection. It does not by itself
claim that the design meets a separate physical signoff policy.
