# Web application

The dependency-free single-page application has three views:

1. **Project Hub**: a clean landing page and extension contract for more tools.
2. **Circuit Studio**: natural-language generation or RTL import, source and
   netlist tabs, SVG schematic, and structural analysis.
3. **RTL-to-GDS Flow**: configuration, six-stage live progress, milestones,
   artifacts, implementation metrics, and diagnosis.

Serve it through `apps/api/app.py`; opening `index.html` directly does not work
because all views read live state from `/api/*`.

```bash
cd /share/home/yuanwenjie/openroad-platform
./scripts/run_demo.sh
```

The browser UI submits jobs to the durable SQLite queue. The separate worker
runs ORFS so a web-process restart does not lose queued or completed evidence.
