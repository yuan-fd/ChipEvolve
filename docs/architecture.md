# Architecture baseline

The first release is a modular control plane with isolated worker processes.
It is intentionally not a collection of network microservices.

```text
API / CLI
    |
    v
RunRequest contract -> SQLite job queue -> independent worker
                                             |
                                             v
                                      ProcessGuardian
                                             |
                                             v
                                        ORFSRunner
                                             |
                    +------------------------+--------------------+
                    v                        v                    v
              artifacts + hashes      metric/diagnosis      layout views
```

## Invariants

- The web/API process never owns an EDA subprocess.
- Every run has an immutable ID and a separate workspace.
- Timeout and cancellation target the process group, not only `make`.
- A stage passes only when the command returns successfully and its required
  artifacts exist and are non-empty.
- A recoverable GDS export can coexist with a failed implementation gate. The
  result reports `gds_complete=true` and `implementation_valid=false` instead
  of turning a PSM or timing failure into success.
- LLM prose cannot change status, metrics, artifact identity, or provenance.

SQLite is the development baseline. PostgreSQL and a distributed queue can
replace the store without changing `RunRequest` and `RunResult`.

