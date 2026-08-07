# P22 UI revision memory snapshot

The user rejected the previous five-page information architecture as visually
polished but logically unclear. The accepted correction is a six-page product:
Overview, Frontend Design, Backend Design, Extensions, Projects & Results, and
Self-Evolution.

Overview now follows product name/positioning, orange tagline, six concise
capabilities, three actions, video/slides reserve, connected workflow, and a
recommended tutorial. It contains no result counters or extension-card dump.

Frontend is a vertical flow with example/upload/natural-language inputs. The
example API provides eight designs including ALU, controller, UART, and mini
RISC-V. Results show synthesized gate count, port count, Graphviz schematic,
RTL, and netlist. RTLScout/BYOK is an optional lower section. Large synthesized
netlists switch to a deterministic cell/port overview above 120 instances to
avoid Graphviz blocking.

Backend is setup, six physical stages, then layout/evidence. Flow-Agent, TaiWei
3D, ImplCraft, and DPLEvolve are clickable lower extensions. DPLEvolve has an
on-demand long-task dashboard and no automatic start.

Extensions contains Flow-Agent, TaiWei 3D, DPLEvolve, and all six EDACraft
components. Detail views expose current status, inputs, workflow, safety note,
results, and bounded smoke submission where supported.

Projects uses a vertical list and separate full-width detail. Self-Evolution
uses a prominent Knowledge/Runtime observations → GP/BO/RL → Human decision →
Campaign/Runtime feedback diagram; papers and benchmarks are secondary details.

Verification: 190 passed, Node syntax and Python compile passed, live HTTP
health/catalog/HTML passed. User-owned untracked `plan/` files were untouched.
