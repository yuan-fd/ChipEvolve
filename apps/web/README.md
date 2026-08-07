# Web application

The dependency-free single-page application has five bilingual views:

1. **Overview**: formal product positioning, capability summary, media slots,
   connected workflow, and a first-run tutorial.
2. **Frontend Design**: built-in examples, RTL upload, natural-language Spec,
   gate statistics, synthesized schematic, RTL/netlist, and optional RTLScout.
3. **Backend Design**: flow configuration, six-stage progress, layout, QoR,
   reports, artifacts, and specialist implementation entry points.
4. **Projects & Results**: vertical project index and complete per-project detail.
5. **Self-Evolution**: a visual knowledge/observation-to-decision feedback loop,
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
Only complementary Craft capabilities are user-facing: TCADCraft, MoMCraft,
and CktCraft. RTLCraft, EDACode, and ImplCraft adapters remain available for
compatibility and historical evidence, but are not duplicated in the primary
RTL or physical-design interface. Specialist details open inside Backend.

Frontend and Backend use a compact interactive-demo workspace: a centered
single column, top workspace switcher, numbered panel headers, and an explicit
input → configuration → action → status → evidence sequence. Backend baseline
mode queues one Runtime run; Stage-aware Campaign and Agent-guided modes create
bounded review plans without automatic execution. The header `中文 / EN` switch
persists locally, and `?lang=zh` / `?lang=en` can select a language for a shared
review URL.

## Visual system

The interface uses a restrained efficiency-tool style: neutral white/light-gray
surfaces, one blue action color, sans-serif type, compact 4/8-based spacing,
small radii, visible hairlines, and no decorative shadows on ordinary content.
Typography and grouping establish hierarchy before borders. Optional details
stay collapsed until the user selects the corresponding backend branch.

Serve it through `apps/api/app.py`; opening `index.html` directly does not work
because all views read live state from `/api/*`.

```bash
cd /share/home/yuanwenjie/openroad-platform
./scripts/run_demo.sh
```

The browser UI submits jobs to the durable SQLite queue. The separate Workflow
Runtime worker executes registered plugins so a web-process restart does not
lose queued or completed evidence.
