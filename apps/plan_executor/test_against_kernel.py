"""Execution plans against the real kernel, worker, object store and HTTP API."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from openroad_platform_client import KernelClient
from openroad_platform_gateway import GatewayConfig, build_router, make_handler
from openroad_platform_gateway.bootstrap import KernelPaths, build_kernel
from openroad_platform_runtime import RuntimeWorker

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "apps" / "plan_executor"
FAKE_ADAPTER = REPO_ROOT / "tests" / "fixtures" / "fake_adapter.py"
CLIENT_SRC = REPO_ROOT / "core" / "client" / "src"
REVIEWED_COMMIT = "0" * 40


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_plugin(root: Path, plugin_id: str) -> None:
    directory = root / "plugins" / plugin_id
    directory.mkdir(parents=True)
    (directory / f"{plugin_id}.plugin.json").write_text(json.dumps({
        "schema_version": 3,
        "plugin_id": plugin_id,
        "plugin_version": "1.0.0",
        "adapter_entry": [sys.executable, str(FAKE_ADAPTER)],
        "capabilities": ["test.plan"],
        "supported_arch": ["aarch64", "x86_64", "arm64"],
        "artifact_rules": [
            {"kind": "report", "required": True},
            {"kind": "log", "required": False},
        ],
    }), encoding="utf-8")
    (directory / "provenance.json").write_text(json.dumps({
        "license": "MIT", "source_commit": REVIEWED_COMMIT,
    }), encoding="utf-8")
    admissions = root / "admissions"
    admissions.mkdir(exist_ok=True)
    (admissions / f"{plugin_id}.json").write_text(json.dumps({
        "plugin_id": plugin_id,
        "status": "admitted",
        "license_review": "green",
        "approved_commit": REVIEWED_COMMIT,
        "reviewer": "acceptance-test",
        "reason": "test fixture",
    }), encoding="utf-8")


def http(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def wait_for(url: str, *, plan_status: str | None = None,
             timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            status, last = http("GET", url)
        except urllib.error.URLError:
            time.sleep(0.1)
            continue
        if status == 200 and (
            plan_status is None or last.get("plan", {}).get("status") == plan_status
        ):
            return last
        if last.get("plan", {}).get("status") in {
            "failed", "cancelled", "timed_out", "lost",
        }:
            return last
        time.sleep(0.1)
    raise AssertionError(f"{url} did not reach {plan_status}: {last}")


def task(task_id: str, plugin_id: str, behaviour: str) -> dict:
    return {
        "schema_version": 3,
        "task_id": task_id,
        "project_id": "acceptance",
        "design_id": "demo",
        "plugin_id": plugin_id,
        "inputs": {"behaviour": behaviour},
        "expected_artifacts": ["report"],
        "timeout_seconds": 20,
    }


def test_two_plugins_exchange_a_measured_artifact_through_a_plan(tmp_path: Path):
    write_plugin(tmp_path, "producer")
    write_plugin(tmp_path, "consumer")
    kernel = build_kernel(
        KernelPaths.of(
            tmp_path / "state", tmp_path / "plugins", tmp_path / "admissions"
        ),
        allow_anonymous=True,
    )
    router = build_router(GatewayConfig(), kernel)
    kernel_port = free_port()
    from http.server import ThreadingHTTPServer

    kernel_server = ThreadingHTTPServer(
        ("127.0.0.1", kernel_port), make_handler(router)
    )
    threading.Thread(target=kernel_server.serve_forever, daemon=True).start()
    worker = RuntimeWorker(kernel.store, kernel.runtime, idle_seconds=0.02)
    worker_stop = threading.Event()
    worker_thread = threading.Thread(
        target=worker.serve_forever, args=(worker_stop,), daemon=True
    )
    worker_thread.start()

    plan_port = free_port()
    env = dict(os.environ)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(APP_DIR / "src"), str(CLIENT_SRC)]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    app = subprocess.Popen(
        [sys.executable, "-m", "openroad_app_plan_executor",
         "--host", "127.0.0.1", "--port", str(plan_port),
         "--kernel-url", f"http://127.0.0.1:{kernel_port}",
         "--db", str(tmp_path / "plans.sqlite")],
        cwd=str(APP_DIR), env=env, stderr=subprocess.PIPE, text=True,
    )
    plan_base = f"http://127.0.0.1:{plan_port}"
    try:
        wait_for(f"{plan_base}/health")
        status, created = http("POST", f"{plan_base}/plans", {
            "plan_id": "real-kernel-plan",
            "steps": [
                {"step_id": "produce",
                 "task": task("produce-task", "producer", "ok")},
                {"step_id": "consume",
                 "task": task("consume-task", "consumer", "echo_input"),
                 "bindings": [{
                     "from_step": "produce", "artifact_kind": "report",
                     "destination": "inputs/report.json",
                 }]},
            ],
        })
        assert status == 201, created
        plan = wait_for(
            f"{plan_base}/plans/real-kernel-plan", plan_status="succeeded"
        )["plan"]
        assert plan["status"] == "succeeded", plan
        assert plan["execution_valid"] is True

        consumer_run = plan["steps"][1]["run_id"]
        client = KernelClient(f"http://127.0.0.1:{kernel_port}")
        detail = client.run(consumer_run)
        attempt = detail["stages"][0]["attempts"][0]
        assert attempt["inputs"][0]["source_artifact_id"]
        metrics = {metric["name"]: metric for metric in client.metrics(consumer_run)}
        assert metrics["input_bytes"]["value"] > 0
        assert metrics["input_bytes"]["complete"] is True
    finally:
        app.terminate()
        app.wait(timeout=10)
        worker_stop.set()
        worker_thread.join(timeout=5)
        kernel_server.shutdown()
        kernel_server.server_close()
        kernel.store.close()
