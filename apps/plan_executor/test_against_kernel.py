"""Execution plans against the real kernel, worker, object store and HTTP API."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from openroad_platform_client import KernelClient
from openroad_platform_gateway import GatewayConfig, build_router, make_handler
from openroad_platform_gateway.bootstrap import KernelPaths, build_kernel
from openroad_platform_runtime import RuntimeWorker

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "apps" / "plan_executor"
FAKE_ADAPTER = REPO_ROOT / "tests" / "fixtures" / "fake_adapter.py"
CLIENT_SRC = REPO_ROOT / "core" / "client" / "src"
CONTRACTS_SRC = REPO_ROOT / "contracts" / "src"
REVIEWED_COMMIT = "0" * 40


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_plugin(root: Path, plugin_id: str, adapter: Path = FAKE_ADAPTER) -> None:
    directory = root / "plugins" / plugin_id
    directory.mkdir(parents=True)
    (directory / f"{plugin_id}.plugin.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "plugin_id": plugin_id,
                "plugin_version": "1.0.0",
                "adapter_entry": [sys.executable, str(adapter)],
                "capabilities": ["test.plan"],
                "supported_arch": ["aarch64", "x86_64", "arm64"],
                "artifact_rules": [
                    {"kind": "report", "required": True},
                    {"kind": "log", "required": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    (directory / "provenance.json").write_text(
        json.dumps(
            {
                "license": "MIT",
                "source_commit": REVIEWED_COMMIT,
            }
        ),
        encoding="utf-8",
    )
    admissions = root / "admissions"
    admissions.mkdir(exist_ok=True)
    (admissions / f"{plugin_id}.json").write_text(
        json.dumps(
            {
                "plugin_id": plugin_id,
                "status": "admitted",
                "license_review": "green",
                "approved_commit": REVIEWED_COMMIT,
                "reviewer": "acceptance-test",
                "reason": "test fixture",
            }
        ),
        encoding="utf-8",
    )


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


def wait_for(
    url: str, *, plan_status: str | None = None, timeout: float = 30.0
) -> dict:
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
            "failed",
            "cancelled",
            "timed_out",
            "lost",
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


@contextmanager
def services(tmp_path: Path, *, plugins_root=None, admissions_root=None):
    """Real HTTP services, worker and durable state shared by acceptance tests."""
    kernel = build_kernel(
        KernelPaths.of(
            tmp_path / "state",
            plugins_root or tmp_path / "plugins",
            admissions_root or tmp_path / "admissions",
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
        [str(APP_DIR / "src"), str(CLIENT_SRC), str(CONTRACTS_SRC)]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    app_log = (tmp_path / "plan-service.log").open("w", encoding="utf-8")
    app = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "openroad_app_plan_executor",
            "--host",
            "127.0.0.1",
            "--port",
            str(plan_port),
            "--kernel-url",
            f"http://127.0.0.1:{kernel_port}",
            "--db",
            str(tmp_path / "plans.sqlite"),
        ],
        cwd=str(APP_DIR),
        env=env,
        stderr=app_log,
        text=True,
    )
    plan_base = f"http://127.0.0.1:{plan_port}"
    try:
        wait_for(f"{plan_base}/health")
        yield plan_base, KernelClient(f"http://127.0.0.1:{kernel_port}")
    finally:
        app.terminate()
        app.wait(timeout=10)
        app_log.close()
        worker_stop.set()
        worker_thread.join(timeout=10)
        kernel_server.shutdown()
        kernel_server.server_close()
        assert not worker_thread.is_alive(), "acceptance left a live worker"
        kernel.store.close()


def test_two_plugins_exchange_a_measured_artifact_through_a_plan(tmp_path: Path):
    write_plugin(tmp_path, "producer")
    write_plugin(tmp_path, "consumer")
    script = tmp_path / "place.tcl"
    patch = tmp_path / "candidate.patch"
    script.write_text("set place_density 0.72\n", encoding="utf-8")
    patch.write_text("candidate patch\n", encoding="utf-8")
    with services(tmp_path) as (plan_base, client):
        status, created = http(
            "POST",
            f"{plan_base}/plans",
            {
                "plan_id": "real-kernel-plan",
                "steps": [
                    {
                        "step_id": "produce",
                        "task": {
                            **task("produce-task", "producer", "echo_agent_inputs"),
                            "inputs": {
                                "behaviour": "echo_agent_inputs",
                                "capability": "script_execution",
                                "script_path": "inputs/place.tcl",
                                "patch_path": "inputs/candidate.patch",
                            },
                            "parameters": {"density": 0.72},
                            "staged_inputs": [
                                {
                                    "source": str(script),
                                    "destination": "inputs/place.tcl",
                                },
                                {
                                    "source": str(patch),
                                    "destination": "inputs/candidate.patch",
                                },
                            ],
                        },
                    },
                    {
                        "step_id": "consume",
                        "task": task("consume-task", "consumer", "echo_input"),
                        "bindings": [
                            {
                                "from_step": "produce",
                                "artifact_kind": "report",
                                "destination": "inputs/report.json",
                            }
                        ],
                    },
                ],
            },
        )
        assert status == 201, created
        plan = wait_for(f"{plan_base}/plans/real-kernel-plan", plan_status="succeeded")[
            "plan"
        ]
        assert plan["status"] == "succeeded", plan
        assert plan["execution_valid"] is True

        producer_run = plan["steps"][0]["run_id"]
        producer_artifacts = client.artifacts(producer_run)
        report = next(a for a in producer_artifacts if a["kind"] == "report")
        assert report["sha256"]

        consumer_run = plan["steps"][1]["run_id"]
        report_excerpt = client.artifact_excerpt(producer_run, report["artifact_id"])
        assert '"capability": "script_execution"' in report_excerpt["text"]
        assert '"density": 0.72' in report_excerpt["text"]
        detail = client.run(consumer_run)
        attempt = detail["stages"][0]["attempts"][0]
        assert attempt["inputs"][0]["source_artifact_id"]
        metrics = {metric["name"]: metric for metric in client.metrics(consumer_run)}
        assert metrics["input_bytes"]["value"] > 0
        assert metrics["input_bytes"]["complete"] is True


@pytest.mark.parametrize("mode", ["script", "patch_benchmark", "build_failure"])
def test_agent_generated_code_is_executed_and_measured(tmp_path: Path, mode: str):
    adapter = REPO_ROOT / "examples" / "research-toolkit" / "adapter.py"
    write_plugin(tmp_path, "research-example", adapter)
    manifest_path = (
        tmp_path / "plugins" / "research-example" / "research-example.plugin.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["environment"] = {"PATH": "/usr/bin:/bin"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    source = tmp_path / "experiment.py"
    source.write_text(
        "import json\nfrom pathlib import Path\n"
        "task = json.loads(Path('task.json').read_text())\n"
        "Path('report.json').write_text(json.dumps({'value': "
        "task['parameters']['seed'] * 7}))\nprint('agent-script-ran')\n",
        encoding="utf-8",
    )
    experiment = task("generated-code", "research-example", "unused")
    experiment["inputs"] = {"capability": "script", "script_path": "experiment.py"}
    experiment["parameters"] = {"seed": 6}
    experiment["staged_inputs"] = [{"source": str(source), "destination": source.name}]
    if mode != "script":
        compiler, patch_tool = shutil.which("cc"), shutil.which("patch")
        if not compiler or not patch_tool:
            pytest.skip("real patch/build acceptance requires cc and patch")
        source = tmp_path / "candidate.c"
        source.write_text('#include <stdio.h>\nint main(void) { puts("21"); }\n')
        patch = tmp_path / "candidate.patch"
        replacement = (
            'int main(void) { puts("42"); }'
            if mode == "patch_benchmark"
            else "invalid C"
        )
        patch.write_text(
            "--- candidate.c\n+++ candidate.c\n@@ -1,2 +1,2 @@\n"
            ' #include <stdio.h>\n-int main(void) { puts("21"); }\n+'
            + replacement
            + "\n",
            encoding="utf-8",
        )
        experiment["inputs"] = {
            "capability": "patch_benchmark",
            "source_path": source.name,
            "patch_path": patch.name,
            "compiler": compiler,
            "patch_tool": patch_tool,
        }
        experiment["staged_inputs"] = [
            {"source": str(path), "destination": path.name} for path in (source, patch)
        ]
    original = source.read_bytes()
    with services(tmp_path) as (base, client):
        code, response = http(
            "POST",
            base + "/plans",
            {
                "plan_id": "generated-experiment",
                "steps": [{"step_id": "experiment", "task": experiment}],
            },
        )
        assert code == 201, response
        plan = wait_for(base + "/plans/generated-experiment", plan_status="succeeded")[
            "plan"
        ]
        run_id = plan["steps"][0]["run_id"]
        if mode == "build_failure":
            assert plan["status"] == "failed", plan
            assert plan["failure"]["category"] == "build_error"
            assert plan["failure"]["source"] == "plugin"
        else:
            assert plan["status"] == "succeeded", plan
            report = next(a for a in client.artifacts(run_id) if a["kind"] == "report")
            result = json.loads(
                client.artifact_excerpt(run_id, report["artifact_id"])["text"]
            )
            assert result["value"] == 42
            if mode == "patch_benchmark":
                assert result["baseline"] == 21
            assert client.metrics(run_id, complete_only=True)[0]["value"] == 42
        attempt = client.run(run_id)["stages"][0]["attempts"][0]
        assert attempt["inputs"][0]["sha256"] == hashlib.sha256(original).hexdigest()
        assert source.read_bytes() == original
        assert client.logs(run_id)
        client.timeline(run_id)
