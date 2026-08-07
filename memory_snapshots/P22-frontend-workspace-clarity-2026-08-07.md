# P22 frontend workspace clarity handoff

- Frontend follows the task order input → result → optional optimization →
  Runtime status → evidence.
- RTL/netlist display is white, dark, line-numbered, wrapped, and applies
  formatting only in the browser. Registered and downloaded artifacts remain
  byte-for-byte unchanged.
- RTLScout is a bounded benchmark experiment, not an implicit optimizer for the
  currently selected uploaded RTL. The current web path supports only the
  official `simple_adder` deterministic offline demo and real Verilator/Yosys.
- Connecting a Provider only creates metadata plus an in-memory secret handle;
  it never starts a run. External HTTP review disables BYOK execution.
- RTLScout Dashboard never fabricates candidates. It reads Runtime run detail,
  artifacts, and upstream `all_evals`; queued and missing-worker states remain
  visible as such.
- RTLCraft/EDACode are independent EDACraft alternatives. `pendingExtension`
  makes their Frontend links safe even when the extension catalog is still
  loading.
- Full regression: 193 passed. Review server: port 8000. User-owned `plan/`
  remains untouched and uncommitted.
