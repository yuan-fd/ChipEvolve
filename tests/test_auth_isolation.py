from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from apps.api.app import ApiState, build_server
from openroad_platform_contracts import PortSpec
from openroad_platform_scheduler import SpecProposal


ROOT = Path(__file__).resolve().parents[1]


def client() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def request(opener: urllib.request.OpenerDirector, base: str, path: str,
            *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    value = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with opener.open(value, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_public_overview_login_and_two_user_design_run_isolation(tmp_path: Path) -> None:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin/yosys",
        runtime_db_path=tmp_path / "runtime.db",
        optimization_db_path=tmp_path / "optimization.db",
        load_taiwei_plugin=False,
    )
    server = build_server("127.0.0.1", 0, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    anonymous, alice, bob = client(), client(), client()
    try:
        status, platform = request(anonymous, base, "/api/platform")
        assert status == 200 and platform["counts"]["designs"] == 0
        assert request(anonymous, base, "/api/designs")[0] == 401

        status, alice_session = request(alice, base, "/api/auth/register", method="POST", body={
            "username": "alice", "password": "alice-pass-123",
        })
        assert status == 201 and alice_session["authenticated"] is True
        assert alice_session["developer"] is True
        assert alice_session["user"]["role"] == "developer"
        status, design = request(alice, base, "/api/designs/import", method="POST", body={
            "filename": "alice_top.v",
            "rtl_source": "module alice_top(input a, output y); assign y = ~a; endmodule\n",
        })
        assert status == 201 and design["module"] == "alice_top"

        status, bob_session = request(bob, base, "/api/auth/register", method="POST", body={
            "username": "bob", "password": "bob-pass-12345",
        })
        assert status == 201 and bob_session["user"]["id"] != alice_session["user"]["id"]
        assert bob_session["developer"] is False
        assert request(bob, base, "/api/designs")[1]["designs"] == []
        assert request(bob, base, f"/api/designs/{design['id']}")[0] == 404

        status, rejected = request(
            alice, base, "/api/v2/closed-loops", method="POST",
            body={"design_id": design["id"], "repetitions": 3, "max_rounds": 3},
        )
        assert status == 400
        assert "does not accept manual search controls" in rejected["error"]

        status, submitted = request(
            alice, base, "/api/v2/closed-loops", method="POST",
            body={"design_id": design["id"], "objective_profile": "balanced"},
        )
        assert status == 201
        pipeline_id = submitted["pipeline_id"]
        run_ids = submitted["state"]["active_run_ids"]
        assert len(run_ids) == 3
        alice_runs = request(alice, base, "/api/runtime/runs")[1]["runs"]
        assert {item["run_id"] for item in alice_runs} == set(run_ids)
        assert request(bob, base, "/api/runtime/runs")[1]["runs"] == []
        assert request(bob, base, f"/api/runtime/runs/{run_ids[0]}")[0] == 404
        assert request(bob, base, f"/api/v2/closed-loops/{pipeline_id}")[0] == 404
        assert request(alice, base, f"/api/runtime/runs/{run_ids[0]}")[1]["wait"]["people_ahead"] == 0
        status, rejected_resume = request(
            alice, base,
            f"/api/v2/closed-loops/{pipeline_id}/run-to-boundary",
            method="POST", body={"max_transitions": 1, "seed": 7},
        )
        assert status == 400
        assert "accepts no transition" in rejected_resume["error"]
        assert request(alice, base, "/api/runtime/runs/from-design", method="POST",
                       body={"design_id": design["id"]})[0] == 404
        for removed in (
            "/api/tasks/compile",
            "/api/extensions/rtlscout/runs",
            "/api/optimization/studies/legacy/recommend",
            "/api/optimization/studies/legacy/interaction-shadow",
            "/api/optimization/studies/legacy/calibrate",
            "/api/runtime/runs/legacy/collect-learning",
            "/api/evolution/auto-reflect",
            "/api/evolution/hypotheses",
            "/api/four-gate/baseline",
            "/api/four-gate/legacy/propose",
            "/api/spec/sessions/legacy/execute",
            "/api/spec/sessions/legacy/register-rtl",
        ):
            assert request(alice, base, removed, method="POST", body={})[0] == 404
        status, rejected_rtl_policy = request(
            alice, base, "/api/rtl/specs/not-needed/run-to-baseline", method="POST",
            body={"max_steps": 99, "api_key": "forbidden"},
        )
        assert status == 400
        assert "accepts no model" in rejected_rtl_policy["error"]

        alice_results = request(alice, base, "/api/platform/results")[1]
        bob_results = request(bob, base, "/api/platform/results")[1]
        assert alice_results["counts"]["designs"] == 1
        assert alice_results["counts"]["runtime_runs"] == 3
        assert bob_results["counts"]["designs"] == 0
        assert bob_results["counts"]["runtime_runs"] == 0

        status, profile = request(alice, base, "/api/providers", method="POST", body={
            "base_url": "https://api.openai.com/v1",
            "model": "review-model",
            "api_key": "test-session-key-not-real",
        })
        # The old BYOK API is not merely disabled in the browser: it is no
        # longer routed by the service, so a supplied key cannot become a
        # provider profile or cross a tenant boundary.
        assert status == 404
        assert request(alice, base, "/api/providers")[0] == 404
        assert request(bob, base, "/api/providers")[0] == 404

        spec_id = state.spec_store.create(
            project_id="openroad-platform", design_id=None,
            provider="test-provider", model="test-model",
        )
        state.spec_store.append_exchange(spec_id, "Specify an inverter", SpecProposal(
            objective="Specify inverter RTL", functionality="y is the inverse of a",
            top="generated_top", clock=None, reset=None,
            target_platform="nangate45", target_stage="finish",
            clock_period_ns=10.0, core_utilization_pct=20.0,
            place_density=0.5,
            ports=(PortSpec("a", "input", 1), PortSpec("y", "output", 1)),
            missing_fields=(), assumptions=(), clarification_questions=(),
            ready_for_execution=True,
        ), provider="test-provider", model="test-model")
        state.auth.bind_resource("spec_session", spec_id, alice_session["user"]["id"])
        status, registered = request(
            alice, base, f"/api/spec/sessions/{spec_id}/materialize-spec",
            method="POST", body={"confirmed": True},
        )
        assert status == 201 and registered["spec"]["top"] == "generated_top"
        assert request(bob, base, f"/api/rtl/specs/{registered['spec']['spec_id']}/lineage")[0] == 404

        assert request(
            alice, base, "/api/campaigns/stage-aware", method="POST",
            body={"design_id": design["id"]},
        )[0] == 404
        assert request(alice, base, "/api/optimization/auto",
                       method="POST", body={})[0] == 404
        assert request(bob, base, "/api/runtime/runs")[1]["runs"] == []

        status, bob_design = request(bob, base, "/api/designs/import", method="POST", body={
            "filename": "bob_top.v",
            "rtl_source": "module bob_top(input a, output y); assign y = a; endmodule\n",
        })
        assert status == 201
        assert all(item["id"] != bob_design["id"]
                   for item in request(alice, base, "/api/designs")[1]["designs"])
        all_results = request(alice, base, "/api/platform/results?scope=all")[1]
        bob_records = [item for item in all_results["records"]
                       if item["id"] == bob_design["id"]]
        assert bob_records[0]["owner_username"] == "bob"
        assert request(alice, base, f"/api/designs/{bob_design['id']}")[0] == 200
        assert request(bob, base, "/api/developer/users")[0] == 403
        assert len(request(alice, base, "/api/developer/users")[1]["users"]) == 2

        status, specialist = request(
            bob, base, "/api/extensions/edacraft/momcraft/run", method="POST", body={
                "design_id": bob_design["id"], "length_mm": 3.0,
                "width_mm": .4, "height_mm": .2, "eps_eff": 4.1,
                "mesh_segments": 6, "frequency_ghz": 2.4,
            },
        )
        assert status == 201
        specialist_task = specialist["run"]["run"]["task_spec"]
        assert specialist_task["parameters"]["frequency_ghz"] == 2.4
        assert specialist_task["labels"]["linked_design_id"] == bob_design["id"]
        assert request(
            bob, base, "/api/extensions/edacraft/cktcraft/run", method="POST",
            body={"design_id": bob_design["id"],
                  "spice_netlist": ".include /tmp/model.lib\n.op\n.end"},
        )[0] == 400

        assert request(alice, base, "/api/auth/logout", method="POST", body={})[0] == 200
        assert request(alice, base, "/api/designs")[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_auth_store_rejects_duplicate_and_wrong_password(tmp_path: Path) -> None:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db",
 load_taiwei_plugin=False,
    )
    session, token = state.auth.register("researcher", "strong-pass-123")
    assert session.legacy_access is True and state.auth.resolve(token) is not None
    with pytest.raises(ValueError, match="already registered"):
        state.auth.register("Researcher", "strong-pass-456")
    with pytest.raises(ValueError, match="Invalid username or password"):
        state.auth.login("researcher", "wrong-password")
