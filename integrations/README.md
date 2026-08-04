# Integration policy

External projects are runner or optimizer adapters, not copied source folders.
Every adapter must pin an upstream commit, record its license, run in an
isolated workspace, honor timeout/cancellation, and pass a contract smoke test.

`plugin-manifest-v1.schema.json` documents the v1 manifest wire shape.
`examples/echo.plugin.json` and `examples/echo_adapter.py` are the executable
conformance example. A manifest argument beginning with `./` is resolved
relative to the manifest file; other argv entries are preserved exactly.

The P1 subprocess boundary assumes pinned, reviewed adapters. Controlled cwd,
an environment allowlist, process-group termination, and artifact path checks
do not constitute an operating-system filesystem sandbox. Untrusted adapters
must wait for a separately approved container/namespace isolation policy.
