# Plugin conformance CI

Plugin repositories must run their platform acceptance tests outside the
platform repository. The platform supplies the protocol and a minimal test
harness; each plugin supplies its own semantic tests and toolchain setup.

## Required matrix

The CI job runs the plugin's tests against an explicitly identified platform
checkout and records the platform revision. It must cover:

- manifest and admission/provenance validation;
- independent process launch and successful result;
- honest plugin failure and malformed result;
- missing and out-of-workspace artifacts;
- registered input reading and progress output;
- timeout, cancellation, and declared resource breach;
- no import of platform packages by the plugin process;
- no writes to a shared toolchain directory.

The workflow must skip real-toolchain tests when the toolchain is unavailable;
it must not report those tests as passing. A nightly or manually dispatched job
should run the real toolchain matrix.

## Minimal workflow shape

```yaml
name: platform-conformance
on: [push, pull_request, workflow_dispatch]
jobs:
  protocol:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@v4
        with:
          repository: <platform-repository>
          path: platform
      - run: python3 -m pip install -U pytest
      - run: python3 -m pytest -q tests/test_against_platform.py
        env:
          AGENTICEDA_PLATFORM: ${{ github.workspace }}/platform
```

The placeholder repository and toolchain installation are plugin-repository
configuration. No plugin-specific branch belongs in the runtime.
