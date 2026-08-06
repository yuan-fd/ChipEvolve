# P18 EDACraft bounded-real acceptance

P18 passed five independent Workflow Runtime runs against EDACraft commit
`739eee0f3ced8fc3cbb6f01b6cc89414758fd898`.

- CktCraft compiled `rfsim v0.2.0` and solved the upstream resistor-divider
  operating point: `v(in)=5.0 V`, `v(mid)=4.2 V`, `i(v1)=0.4 mA`.
- MoMCraft compiled its C++/pybind core and executed a deliberately small
  one-frequency, four-segment microstrip solve. Its Touchstone file and numeric
  summary are registered artifacts. This is not an EM sign-off claim.
- TCADCraft executed upstream geometry and three physics invariants. The pinned
  full solver was not claimed because its implementation references declarations
  absent from its header; this upstream blocker is preserved in the lock file.
- EDACode emitted a review-only proposal with no registered Bash, file-write, or
  background tools. RTLCraft retained its real DSL-to-SystemVerilog smoke.

The ignored replay bundle is `artifacts/p18-real-20260806/`; its acceptance
summary and Runtime database hashes are recorded in the adjacent JSON evidence.
Build overlays and binary hashes are in `integrations/edacraft/`.
