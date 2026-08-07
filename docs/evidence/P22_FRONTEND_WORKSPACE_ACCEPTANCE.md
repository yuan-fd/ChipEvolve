# P22 frontend workspace clarity acceptance

Date: 2026-08-07

## Delivered behavior

- Verilog and synthesized netlist views now use a white background, dark text,
  line numbers, wrapping, and display-only formatting for minified one-line HDL.
  Downloaded and registered source artifacts are not rewritten.
- RTLScout is presented as one explicit sequence: choose a bounded experiment,
  optionally connect a provider profile, submit to Workflow Runtime, monitor the
  run, and inspect verified candidate evidence.
- The RTLScout workflow identifies the LLM as a proposal source and
  Verilator/Yosys as the correctness and cost authorities.
- The run dashboard reads status, candidate rows, cost, improvement, duration,
  and artifact links from Runtime and `rtlscout_result.json`. Missing evidence
  remains an honest empty or waiting state.
- The local offline path is restricted to the audited `simple_adder` fixture,
  official deterministic fake model, at most eight steps, and fast Yosys cost
  metrics. No run was launched during this UI revision.
- Provider connection is visually and behaviorally separate from run launch.
  Custom-provider execution remains blocked on the HTTP review service until an
  HTTPS worker secret bridge exists.
- RTLCraft and EDACode are identified as frontend alternatives rather than
  RTLScout stages. Their buttons now route to Extensions and open the selected
  component's purpose, required input, workflow, readiness, and available
  action after asynchronous catalog loading.

## Verification

- `node --check apps/web/assets/app.js`: passed
- `python3 -m py_compile apps/api/app.py`: passed
- `python3 -m pytest -q`: **193 passed**
- `git diff --check`: passed
- 1440 px and 390 px Frontend browser captures reviewed
- `/api/extensions/rtlscout`: pinned source/toolchain ready
- No credential, EDA run, model call, push, or public deployment occurred
