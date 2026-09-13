"""DSE Lab: submit a parameter sweep and compare the evidence.

A design-space exploration console.  It composes tasks, tracks them through the
kernel, and compares the resulting evidence.

Three boundaries this app keeps, each of which is a rule of the architecture
rather than a preference:

* **It owns its own database and nothing else.**  The sweep definitions are
  here; every run, artifact and metric belongs to the kernel and is read back
  through the client (G5).  There is no second copy of the evidence.
* **It does not validate parameters.**  The allowlist, the bounds and the
  cross-parameter rules live in the plugin that consumes them.  A console that
  re-implemented them would be a second source of truth, and the two would
  disagree the first time a bound changed.  An invalid point is submitted, and
  the rejection is reported as a result.
* **It asserts nothing about quality.**  It shows measured values and where they
  came from.  A point whose metric cites no artifact is shown as unsourced, and
  a point that failed is shown as failed -- it is never dropped from the
  comparison, because a sweep that quietly omits its failures flatters whichever
  policy produced fewer of them.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from openroad_platform_client import KernelClient, KernelError, KernelUnavailable

SERVICE_NAME = "dse_lab"
DEFAULT_PORT = 8820

#: This app's own database.  Named so that it cannot be confused with any of the
#: kernel's, which this app must never open.
DB_FILENAME = "dse_lab.sqlite"

#: A sweep is bounded.  An unbounded sweep is a way for one caller to occupy the
#: whole worker pool, and the budget is part of the experiment definition anyway.
MAX_POINTS_PER_SWEEP = 64

#: How many runs a comparison will read.  Bounded for the same reason: the
#: comparison is rendered, not computed, so it does not need the whole history.
MAX_COMPARISON_RUNS = 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plugin_id TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS points (
    point_id TEXT PRIMARY KEY,
    sweep_id TEXT NOT NULL REFERENCES sweeps(sweep_id),
    ordinal INTEGER NOT NULL,
    parameters_json TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    UNIQUE(sweep_id, ordinal)
);
"""


class LabError(Exception):
    """The lab refused the request, with a status the caller can act on."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class SweepStore:
    """This app's own state.  No kernel table is touched."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = DELETE")
        return connection

    # -- sweeps -----------------------------------------------------------

    def create_sweep(self, *, name: str, plugin_id: str,
                     inputs: dict[str, Any],
                     points: list[dict[str, Any]]) -> str:
        if not name.strip():
            raise LabError(400, "a sweep name is required")
        if not plugin_id.strip():
            raise LabError(400, "a plugin id is required")
        if not points:
            raise LabError(400, "a sweep needs at least one point")
        if len(points) > MAX_POINTS_PER_SWEEP:
            raise LabError(
                400, f"a sweep is limited to {MAX_POINTS_PER_SWEEP} points"
            )
        import time

        sweep_id = f"sweep-{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO sweeps (sweep_id, name, plugin_id, inputs_json, "
                "created_at) VALUES (?,?,?,?,?)",
                (sweep_id, name.strip(), plugin_id.strip(),
                 json.dumps(inputs, sort_keys=True), time.time()),
            )
            for ordinal, parameters in enumerate(points):
                connection.execute(
                    "INSERT INTO points (point_id, sweep_id, ordinal, "
                    "parameters_json) VALUES (?,?,?,?)",
                    (f"point-{uuid.uuid4().hex}", sweep_id, ordinal,
                     json.dumps(parameters, sort_keys=True)),
                )
        return sweep_id

    def list_sweeps(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sweeps ORDER BY created_at DESC"
            ).fetchall()
        return [self._sweep_row(row) for row in rows]

    def get_sweep(self, sweep_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sweeps WHERE sweep_id = ?", (sweep_id,)
            ).fetchone()
            if row is None:
                raise LabError(404, f"unknown sweep {sweep_id!r}")
            points = connection.execute(
                "SELECT * FROM points WHERE sweep_id = ? ORDER BY ordinal",
                (sweep_id,),
            ).fetchall()
        sweep = self._sweep_row(row)
        sweep["points"] = [self._point_row(item) for item in points]
        return sweep

    def record_submission(self, point_id: str, *, task_id: str,
                          run_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE points SET task_id = ?, run_id = ? WHERE point_id = ?",
                (task_id, run_id, point_id),
            )

    @staticmethod
    def _sweep_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sweep_id": row["sweep_id"], "name": row["name"],
            "plugin_id": row["plugin_id"],
            "inputs": json.loads(row["inputs_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _point_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "point_id": row["point_id"], "ordinal": row["ordinal"],
            "parameters": json.loads(row["parameters_json"]),
            "task_id": row["task_id"], "run_id": row["run_id"],
        }


# --------------------------------------------------------------------------
# the sweep lifecycle
# --------------------------------------------------------------------------

def submit_sweep(client: KernelClient, store: SweepStore, *, name: str,
                 plugin_id: str, inputs: dict[str, Any],
                 points: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a sweep and submit every point.

    A point the kernel refuses is recorded as refused rather than aborting the
    sweep: the refusal is a result about that point, and the remaining points
    are independent experiments.
    """
    sweep_id = store.create_sweep(name=name, plugin_id=plugin_id,
                                  inputs=inputs, points=points)
    sweep = store.get_sweep(sweep_id)
    for point in sweep["points"]:
        task = {
            "schema_version": 2,
            "task_id": f"{sweep_id}-p{point['ordinal']}",
            "project_id": sweep["plugin_id"],
            "design_id": str(inputs.get("design") or "unknown"),
            "plugin_id": plugin_id,
            "inputs": dict(inputs),
            "parameters": dict(point["parameters"]),
        }
        try:
            created = client.submit(task, idempotent=True)
        except (KernelError, KernelUnavailable) as exc:
            store.record_submission(point["point_id"], task_id=task["task_id"],
                                    run_id=f"refused: {exc}")
            continue
        store.record_submission(point["point_id"], task_id=task["task_id"],
                                run_id=created["run"]["run_id"])
    return store.get_sweep(sweep_id)


def compare_sweep(client: KernelClient, store: SweepStore,
                  sweep_id: str) -> dict[str, Any]:
    """Each point with its run's measured metrics and where they came from.

    Points are returned in submission order and none is omitted.  A failed point
    and a point with unsourced metrics are both visible, because a comparison
    that shows only the successful points is a comparison of whichever policy
    happened to fail less often.
    """
    sweep = store.get_sweep(sweep_id)
    rows = []
    seen_runs = 0
    for point in sweep["points"]:
        entry: dict[str, Any] = {
            "ordinal": point["ordinal"],
            "parameters": point["parameters"],
            "run_id": point["run_id"],
        }
        run_id = point["run_id"] or ""
        if run_id.startswith("refused:") or not run_id:
            entry["status"] = "refused"
            entry["reason"] = run_id.removeprefix("refused: ") or "not submitted"
            entry["metrics"] = []
            rows.append(entry)
            continue
        if seen_runs >= MAX_COMPARISON_RUNS:
            entry["status"] = "not_compared"
            entry["reason"] = f"comparison is limited to {MAX_COMPARISON_RUNS} runs"
            entry["metrics"] = []
            rows.append(entry)
            continue
        seen_runs += 1
        try:
            detail = client.run(run_id)
            metrics = client.metrics(run_id)
        except KernelError as exc:
            entry["status"] = "unavailable"
            entry["reason"] = str(exc)
            entry["metrics"] = []
            rows.append(entry)
            continue
        entry["status"] = detail["status"]
        entry["terminal_reason"] = detail.get("terminal_reason")
        entry["metrics"] = metrics
        entry["unsourced"] = sum(1 for m in metrics if not m["complete"])
        rows.append(entry)

    measured = [row for row in rows if row["status"] == "succeeded"]
    return {
        "sweep": {k: v for k, v in sweep.items() if k != "points"},
        "points": rows,
        "summary": {
            "points": len(rows),
            "succeeded": len(measured),
            "failed": sum(1 for r in rows if r["status"] == "failed"),
            "refused": sum(1 for r in rows if r["status"] == "refused"),
            "unsourced_metrics": sum(r.get("unsourced", 0) for r in rows),
        },
        "claim_boundary": (
            "This is a comparison of measured evidence, not a claim that any "
            "point is better. Selection needs a frozen protocol and repeated "
            "runs, which is the evaluator's business rather than this app's."
        ),
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"openroad-app-{SERVICE_NAME}/0.1"

    client: KernelClient
    store: SweepStore

    def do_GET(self) -> None:  # noqa: N802 - required by the base class
        parsed = urllib.parse.urlparse(self.path)
        try:
            self._send(200, self._get(parsed.path))
        except LabError as exc:
            self._send(exc.status, {"error": exc.message})
        except KernelUnavailable as exc:
            self._send(503, {"error": str(exc)})
        except KernelError as exc:
            self._send(exc.status or 502, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 - required by the base class
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self._read_body()
            self._send(201, self._post(parsed.path, body))
        except LabError as exc:
            self._send(exc.status, {"error": exc.message})
        except KernelUnavailable as exc:
            self._send(503, {"error": str(exc)})
        except KernelError as exc:
            self._send(exc.status or 502, {"error": str(exc)})

    def _get(self, path: str) -> dict[str, Any]:
        if path == "/health":
            return {"app": SERVICE_NAME, "status": "ok", "kernel": self.client.health()}
        if path in ("/", "/sweeps"):
            return {"sweeps": self.store.list_sweeps()}
        if path.startswith("/sweeps/") and path.endswith("/comparison"):
            sweep_id = urllib.parse.unquote(path[len("/sweeps/"):-len("/comparison")])
            return compare_sweep(self._borrow(), self.store, sweep_id)
        if path.startswith("/sweeps/"):
            return {"sweep": self.store.get_sweep(
                urllib.parse.unquote(path[len("/sweeps/"):]))}
        raise LabError(404, "not found")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/sweeps":
            points = body.get("points")
            if not isinstance(points, list) or not all(
                isinstance(item, dict) for item in points
            ):
                raise LabError(400, "points must be a list of parameter objects")
            inputs = body.get("inputs")
            if not isinstance(inputs, dict):
                raise LabError(400, "inputs must be an object")
            sweep = submit_sweep(
                self._borrow(), self.store,
                name=str(body.get("name") or ""),
                plugin_id=str(body.get("plugin_id") or ""),
                inputs=inputs, points=points,
            )
            return {"sweep": sweep}
        raise LabError(404, "not found")

    def _borrow(self) -> KernelClient:
        """Forward the caller's token rather than inventing one.

        The lab holds no credentials, so it cannot submit or read on behalf of
        anyone but the caller.
        """
        header = self.headers.get("Authorization", "") or ""
        self.client.token = (header[7:].strip()
                             if header.lower().startswith("bearer ") else None)
        return self.client

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1024 * 1024:
            raise LabError(413, "request body is too large")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LabError(400, "request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise LabError(400, "request body must be a JSON object")
        return payload

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def serve(*, host: str, port: int, kernel_url: str, db_path: Path) -> None:
    handler = type("BoundHandler", (Handler,), {
        "client": KernelClient(kernel_url), "store": SweepStore(db_path),
    })
    ThreadingHTTPServer((host, port), handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DSE Lab")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--kernel-url", default="http://127.0.0.1:8700")
    parser.add_argument("--db", default=DB_FILENAME,
                        help="this app's own database")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, kernel_url=args.kernel_url,
          db_path=Path(args.db))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
