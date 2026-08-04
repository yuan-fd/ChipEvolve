# API and web entry point

`app.py` serves the project hub from `apps/web` and exposes the local control
plane. It uses only the Python standard library and never launches EDA tools
inside an HTTP request; an independent scheduler worker consumes queued jobs.

Main endpoints:

- `GET /api/health`, `/api/projects`, `/api/designs`
- `GET /api/designs/<id>` and its `source` and `schematic.svg` resources
- `POST /api/designs/generate` for natural-language generation
- `POST /api/designs/import` for an existing RTL design
- `POST /api/runs/from-design` or `POST /api/runs`
- `GET /api/runs` and `GET /api/runs/<id>`
- `POST /api/runs/<id>/cancel`
- `GET /api/runtime/runs`, `GET /api/campaigns`, and their detail/cancel routes
- `POST /api/tasks/compile` for a validated NL→TaskSpec preview (no execution)

Runtime and Campaign live SQLite state defaults to
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

The design layer is implemented by `services/DesignService`. `ICCAD_ROOT` can
select the natural-language generator adapter; generated source and all derived
evidence are copied into this platform's own `var/designs` directory.
