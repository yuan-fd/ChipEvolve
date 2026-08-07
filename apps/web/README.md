# Web application

The dependency-free single-page application has six English views:

1. **Overview**: formal product positioning, capability summary, media slots,
   connected workflow, and a first-run tutorial.
2. **Frontend Design**: built-in examples, RTL upload, natural-language Spec,
   gate statistics, synthesized schematic, RTL/netlist, and optional RTLScout.
3. **Backend Design**: flow configuration, six-stage progress, layout, QoR,
   reports, artifacts, and specialist implementation entry points.
4. **Extensions**: clickable TaiWei 3D, Flow-Agent, DPLEvolve, and six-component
   EDACraft catalog with readiness, inputs, workflows, and Runtime actions.
5. **Projects & Results**: vertical project index and complete per-project detail.
6. **Self-Evolution**: a visual knowledge/observation-to-decision feedback loop,
   human-controlled recommendations, and expandable research records.

The built-in example catalog includes starter circuits plus an ALU, controller,
UART transmitter, and compact RISC-V teaching core. Small netlists use a full
Graphviz connectivity view; large netlists automatically use a deterministic
port/cell overview to keep rendering bounded.

Verilog and gate-netlist tabs use a white, line-numbered, wrapped code reader.
Formatting of minified HDL is display-only; downloads preserve the registered
artifact. RTLScout is organized as configure → optional provider connection →
Runtime submission → run dashboard → verified candidate evidence. Its bundled
offline demo is deliberately limited to the audited `simple_adder` benchmark;
custom provider execution requires HTTPS and a worker-side secret bridge.
RTLCraft and EDACode links open their independent extension detail rather than
being presented as RTLScout stages.

## Visual system

The interface uses a restrained efficiency-tool style: neutral white/light-gray
surfaces, one blue action color, sans-serif type, compact 4/8-based spacing,
small radii, visible hairlines, and no decorative shadows on ordinary content.
Typography and grouping establish hierarchy before borders. Long optional
surfaces such as the DPLEvolve dashboard stay hidden until the user selects the
corresponding extension.

Serve it through `apps/api/app.py`; opening `index.html` directly does not work
because all views read live state from `/api/*`.

```bash
cd /share/home/yuanwenjie/openroad-platform
./scripts/run_demo.sh
```

The browser UI submits jobs to the durable SQLite queue. The separate worker
runs ORFS so a web-process restart does not lose queued or completed evidence.
