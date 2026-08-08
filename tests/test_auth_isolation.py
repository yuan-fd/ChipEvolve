from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from apps.api.app import ApiState, build_server
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
        campaign_db_path=tmp_path / "campaign.db",
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
        status, design = request(alice, base, "/api/designs/import", method="POST", body={
            "filename": "alice_top.v",
            "rtl_source": "module alice_top(input a, output y); assign y = ~a; endmodule\n",
        })
        assert status == 201 and design["module"] == "alice_top"

        status, bob_session = request(bob, base, "/api/auth/register", method="POST", body={
            "username": "bob", "password": "bob-pass-12345",
        })
        assert status == 201 and bob_session["user"]["id"] != alice_session["user"]["id"]
        assert request(bob, base, "/api/designs")[1]["designs"] == []
        assert request(bob, base, f"/api/designs/{design['id']}")[0] == 404

        status, submitted = request(
            alice, base, "/api/runtime/runs/from-design", method="POST",
            body={"design_id": design["id"], "flow_mode": "baseline"},
        )
        assert status == 201
        run_id = submitted["run"]["run_id"]
        alice_runs = request(alice, base, "/api/runtime/runs")[1]["runs"]
        assert [item["run_id"] for item in alice_runs] == [run_id]
        assert request(bob, base, "/api/runtime/runs")[1]["runs"] == []
        assert request(bob, base, f"/api/runtime/runs/{run_id}")[0] == 404
        assert request(alice, base, f"/api/runtime/runs/{run_id}")[1]["wait"]["people_ahead"] == 0

        alice_results = request(alice, base, "/api/platform/results")[1]
        bob_results = request(bob, base, "/api/platform/results")[1]
        assert alice_results["counts"]["designs"] == 1
        assert alice_results["counts"]["runtime_runs"] == 1
        assert bob_results["counts"]["designs"] == 0
        assert bob_results["counts"]["runtime_runs"] == 0

        status, profile = request(alice, base, "/api/providers", method="POST", body={
            "base_url": "https://api.openai.com/v1",
            "model": "review-model",
            "api_key": "test-session-key-not-real",
        })
        assert status == 201 and profile["api_key"] is None
        assert len(request(alice, base, "/api/providers")[1]["profiles"]) == 1
        assert request(bob, base, "/api/providers")[1]["profiles"] == []

        spec_id = state.spec_store.create(
            project_id="openroad-platform", design_id=None,
            provider="test-provider", model="test-model",
        )
        state.spec_store.append_exchange(spec_id, "Generate an inverter", SpecProposal(
            objective="Generate inverter RTL", functionality="y is the inverse of a",
            top="generated_top", clock=None, reset=None,
            target_platform="nangate45", target_stage="finish",
            clock_period_ns=10.0, core_utilization_pct=20.0,
            place_density=0.5,
            rtl_source=("module generated_top(input a, output y); "
                        "assign y = ~a; endmodule\n"),
            missing_fields=(), assumptions=(), clarification_questions=(),
            ready_for_execution=True,
        ), provider="test-provider", model="test-model")
        state.auth.bind_resource("spec_session", spec_id, alice_session["user"]["id"])
        status, registered = request(
            alice, base, f"/api/spec/sessions/{spec_id}/register-rtl",
            method="POST", body={"confirmed": True},
        )
        assert status == 201 and registered["design"]["module"] == "generated_top"
        assert registered["session"]["status"] == "design_registered"
        assert request(bob, base, f"/api/designs/{registered['design']['id']}")[0] == 404

        status, campaign = request(
            alice, base, "/api/campaigns/stage-aware", method="POST", body={
                "design_id": design["id"], "flow_mode": "campaign",
                "target_stage": "synth",
                "parameter_grid": {"core_utilization_pct": [20, 30]},
            },
        )
        assert status == 201 and len(campaign["members"]) == 2
        assert campaign["members"][0]["parameters"]["target_stage"] == "synth"
        campaign_id = campaign["campaign_id"]
        assert request(bob, base, f"/api/campaigns/{campaign_id}")[0] == 404
        status, started = request(
            alice, base, f"/api/campaigns/{campaign_id}/submit",
            method="POST", body={},
        )
        assert status == 201 and len(started["run_ids"]) == 2
        assert started["execution_started"] is True
        assert request(bob, base, "/api/runtime/runs")[1]["runs"] == []

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
        campaign_db_path=tmp_path / "campaign.db", load_taiwei_plugin=False,
    )
    session, token = state.auth.register("researcher", "strong-pass-123")
    assert session.legacy_access is True and state.auth.resolve(token) is not None
    with pytest.raises(ValueError, match="already registered"):
        state.auth.register("Researcher", "strong-pass-456")
    with pytest.raises(ValueError, match="Invalid username or password"):
        state.auth.login("researcher", "wrong-password")
