from __future__ import annotations

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openroad_platform_scheduler import (
    InMemorySecretBroker, OpenAICompatibleSpecProvider, ProviderProfile,
    ProviderProfileStore,
)


PROPOSAL = {
    "objective": "small counter", "functionality": "counter", "top": "counter",
    "clock": "clk", "reset": "rst_n", "target_platform": "nangate45",
    "target_stage": "finish", "clock_period_ns": 10,
    "core_utilization_pct": 10, "place_density": 0.45,
    "ports": [{"name": "clk", "direction": "input", "width": 1}], "missing_fields": [],
    "assumptions": [], "clarification_questions": [], "ready_for_execution": True,
}


class FakeHandler(BaseHTTPRequestHandler):
    mode = "ok"
    seen_auth = ""

    def do_POST(self):  # noqa: N802
        type(self).seen_auth = self.headers.get("Authorization", "")
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.mode == "timeout":
            time.sleep(1.2)
        if self.mode in {"401", "429"}:
            self.send_response(int(self.mode)); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        if self.mode == "invalid":
            self.wfile.write(b"not-json")
        elif self.mode == "large":
            self.wfile.write(b"{" + b"x" * 2000 + b"}")
        elif self.mode == "echo-secret":
            self.wfile.write(json.dumps({"choices": [{"message": {
                "content": "p16-canary-secret"}}]}).encode())
        else:
            self.wfile.write(json.dumps({"choices": [{"message": {"content": json.dumps(PROPOSAL)}}]}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield server
    finally:
        server.shutdown(); thread.join()


def provider_for(tmp_path, server, key="p16-canary-secret", *, mode="ok", timeout=2,
                 cancel_event=None):
    FakeHandler.mode = mode
    broker = InMemorySecretBroker(default_ttl_seconds=60)
    handle = broker.put(key, owner_id="alice", session_id="browser")
    profile = ProviderProfile("local-fake", "alice", "openai-compatible-byok",
                              f"http://127.0.0.1:{server.server_port}/v1", "fake-model",
                              timeout_seconds=timeout, max_response_bytes=1024, max_calls=10,
                              allow_private_endpoint=True)
    store = ProviderProfileStore(tmp_path / f"provider-{mode}.db"); store.save(profile)
    return OpenAICompatibleSpecProvider(profile, broker, handle, owner_id="alice",
                                        session_id="browser", profile_store=store,
                                        cancel_event=cancel_event), store, broker, handle


def test_byok_success_and_canary_never_persists(tmp_path, fake_provider):
    provider, store, broker, handle = provider_for(tmp_path, fake_provider)
    proposal = provider.propose([{"role": "user", "content": "counter"}], {})
    assert proposal.top == "counter"
    assert FakeHandler.seen_auth == "Bearer p16-canary-secret"
    assert "p16-canary-secret" not in store.path.read_bytes().decode("latin1")
    assert broker.describe(handle, owner_id="alice", session_id="browser")["secret_present"]


@pytest.mark.parametrize("mode,pattern", [
    ("401", "HTTP error 401"), ("429", "HTTP error 429"),
    ("invalid", "invalid structured JSON"), ("large", "size limit"),
    ("echo-secret", "secret material"),
])
def test_byok_bounded_failures_do_not_echo_secret(tmp_path, fake_provider, mode, pattern):
    provider, _, _, _ = provider_for(tmp_path, fake_provider, mode=mode)
    with pytest.raises(RuntimeError, match=pattern) as error:
        provider.propose([{"role": "user", "content": "x"}], {})
    assert "p16-canary-secret" not in str(error.value)


def test_byok_timeout_cancel_ttl_and_owner_isolation(tmp_path, fake_provider):
    provider, _, broker, handle = provider_for(tmp_path, fake_provider, mode="timeout", timeout=1)
    with pytest.raises(RuntimeError, match="timed out"):
        provider.propose([{"role": "user", "content": "x"}], {})
    cancel = threading.Event(); cancel.set()
    provider, _, _, _ = provider_for(tmp_path, fake_provider, mode="ok", cancel_event=cancel)
    with pytest.raises(RuntimeError, match="cancelled"):
        provider.propose([{"role": "user", "content": "x"}], {})
    with pytest.raises(PermissionError):
        broker.resolve(handle, owner_id="bob", session_id="browser")
    broker.revoke(handle, owner_id="alice", session_id="browser")
    with pytest.raises(KeyError):
        broker.resolve(handle, owner_id="alice", session_id="browser")
