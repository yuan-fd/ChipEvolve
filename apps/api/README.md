# API and web entry point

`app.py` serves the project hub from `apps/web` and exposes the local control
plane. It uses only the Python standard library and never launches EDA tools
inside an HTTP request; an independent scheduler worker consumes queued jobs.

Main endpoints:

- `GET /api/health`, `/api/projects`, `/api/designs`
- `GET /api/designs/<id>` and its `source` and `schematic.svg` resources
- `POST /api/spec/sessions` for the platform-managed natural-language SpecIR entry
- `POST /api/designs/import` for an existing RTL design
- `POST /api/v2/closed-loops` for the only 2D product start path
- `POST /api/v2/closed-loops/<id>/run-to-boundary` to run/resume repeated baseline and BO/GP exploration
- `GET /api/runtime/runs` and run detail/cancel routes for child-run monitoring

Runtime and autonomous-loop live SQLite state defaults to
`/tmp/openroad-platform-<uid>/`, keeping WAL files off the shared project
filesystem. `OPENROAD_PLATFORM_LOCAL_STATE` may select another node-local
directory. The API only reads state and writes cancellation requests; workers
remain the only owners of execution subprocesses.

Run from the repository root:

```bash
python3 apps/api/app.py --host 0.0.0.0 --port 8000
```

Use `127.0.0.1` instead of `0.0.0.0` when only local access is needed. For a
remote server, use an SSH tunnel as documented in the root `README.md`.

The design layer is implemented by `services/DesignService`. Natural-language
requests enter through SpecIR and the automatic independent-verifier + RTLScout
state machine; there is no second direct-LLM RTL generation endpoint. Generated
source and all derived evidence are copied into this platform's own
`var/designs` directory.
