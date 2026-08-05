# OpenROAD Platform

OpenROAD Platform is a small, extensible project hub for digital-design demos.
The home page stays intentionally simple and currently exposes three complete
workspaces:

- **Circuit Studio**: natural-language circuit generation, RTL, synthesized
  gate-level netlist, SVG schematic, and deterministic structural analysis.
- **RTL-to-GDS Flow**: durable job submission, six native ORFS stages, live
  status, GDS/DEF/netlist artifacts, timing/DRC/power metrics, and physical
  diagnosis.
- **TaiWei 3D**: pinned official ORD/asap7_3D/gcd runs, upper/bottom tier
  state, physical HB-via and cross-tier metrics, hashed GDS/DEF/ODB evidence,
  and replay/toolchain provenance.

The common control plane also includes EDACraft/ImplCraft script generation,
bounded multi-turn Spec-to-GDS sessions, and stage-aware parameter Campaigns.
Schematics use Graphviz, 2D GDS uses KLayout, and the 3D layer view uses real
KLayout polygons rendered as a sampled layer stack.

The natural-language generator is isolated behind `DesignService`. It can use
the proven generator in `../iccad` through the `ICCAD_ROOT` adapter, while all
generated designs, analysis, jobs, and implementation artifacts are owned by
this repository under `var/`. New projects can be added to the hub without
coupling their runtime to the web shell.

## Repository layout

```text
apps/
  api/                  HTTP API, static-site server, design adapter
  web/                  project hub and the two browser workspaces
packages/
  contracts/            stable requests, results, stages, artifacts
  scheduler/            SQLite queue and independent worker
  execution/            ORFS runner, process control, configuration
  analysis/             netlist and physical-result analysis
  visualization/        schematic and layout visualization
workflows/               extension slots for future projects
integrations/            external adapter boundary
tests/                   contracts, API, scheduler, runner, analysis
var/                     generated runtime evidence (git-ignored)
```

## Start the complete demo

Install the local package once, then start both the web process and worker:

```bash
cd /share/home/yuanwenjie/openroad-platform
python3 -m pip install -e '.[test,visualization]'
./scripts/run_demo.sh
```

The script binds the website to `0.0.0.0:8000`; keep it on a trusted network.
The web process only serves the UI and submits durable jobs. The independent
worker is the process that runs Yosys, OpenROAD, and ORFS.

You can also run the two processes in separate terminals:

```bash
python3 apps/api/app.py --host 0.0.0.0 --port 8000
openroad-jobs --db ./var/platform.db worker \
  --orfs-root ../OpenROAD-flow-scripts \
  --openroad-bin ../bin/openroad --yosys-bin ../bin/yosys
```

To open a sealed Workflow Runtime database, pass the node-local SQLite files
explicitly:

```bash
python3 apps/api/app.py --host 127.0.0.1 --port 8000 \
  --runtime-db /tmp/openroad-platform-runtime/runtime.db \
  --campaign-db /tmp/openroad-platform-runtime/campaign.db
```

See `docs/OPERATIONS.md` for the three-chain replay, backup/restore and upgrade
procedure. Live SQLite WAL files must stay on node-local `/tmp`, not this
distributed project filesystem.

Defaults are tuned for the small generated demo circuits: 10% core
utilization and 0.45 placement density. They remain configurable in the Flow
workspace and APIs.

## Open the website

From a machine that can reach this server, open one of its reachable network
addresses on port 8000. On the server itself, use:

```text
http://127.0.0.1:8000
```

`127.0.0.1` always means the machine where the browser is running. If this
repository is on a remote SSH server, create a tunnel on your local computer:

```bash
ssh -N -L 8000:127.0.0.1:8000 yuanwenjie@master
```

Then open `http://127.0.0.1:8000` in the local browser. Replace `master` with
the SSH hostname normally used to reach the server. Do not expose this
dependency-free demo server directly to the public Internet; it has no login
or TLS termination.

## Verify readiness

```bash
curl -fsS http://127.0.0.1:8000/api/health
python3 -m pytest -q
```

P11-P13 acceptance can be replayed into new empty ignored directories:

```bash
python scripts/run_p11_acceptance.py --output-root .tools/p11-acceptance/new
python scripts/run_p12_acceptance.py --output-root .tools/p12-acceptance/new
python scripts/run_p13_acceptance.py --output-root .tools/p13-acceptance/new
```

`execution_ready` and `generator_ready` must both be `true` for the two full
workflows. A successful physical run separately records `synthesizable`,
`implementation_valid`, and `gds_complete`; generated RTL is not claimed to be
functionally verified unless a real functional verification step has run.
