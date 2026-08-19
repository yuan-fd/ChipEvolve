#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configure DeepSeek (via local claude-code-router) as BYOK provider and run a spec case."""
import json, os, sys
from pathlib import Path
ROOT = Path('/share/home/yuanwenjie/openroad-platform')
sys.path.insert(0, str(ROOT))
for s in (ROOT/'packages/contracts/src', ROOT/'packages/execution/src',
          ROOT/'packages/scheduler/src', ROOT/'packages/analysis/src',
          ROOT/'packages/visualization/src'):
    sys.path.insert(0, str(s))
from apps.api.app import ApiState

OWNER = "user-ea0c3d1f4520448d99a22d2dc7f7b250"
api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
print("api_key prefix:", api_key[:8], "...")

state = ApiState(
    ROOT/'var/platform.db', ROOT/'var/uploads', ROOT.parent/'OpenROAD-flow-scripts',
    design_root=ROOT/'var/designs', legacy_root=ROOT.parent/'iccad',
    runtime_db_path=ROOT/'var/public/runtime.db',
    campaign_db_path=ROOT/'var/public/campaign.db',
    optimization_db_path=ROOT/'var/public/optimization.db',
    auth_db_path=ROOT/'var/public/web-auth.db',
    byok_transport_secure=True, load_taiwei_plugin=False,
)

# 1) save provider profile (DeepSeek via local router)
prof = state.save_provider_profile({
    "owner_id": OWNER, "session_id": "byok-deepseek-test",
    "profile_name": "DeepSeek-Local", "base_url": "http://127.0.0.1:3456/v1",
    "model": "DeepSeek/deepseek-v4-flash", "api_key": api_key,
    "allow_private_endpoint": True,
    "timeout_seconds": 120, "max_response_bytes": 1048576, "max_calls": 8,
})
print("profile:", prof["profile_id"], "model:", prof["model"])

# 2) run a small spec case through the BYOK provider
payload = {
    "owner_id": OWNER, "session_id": prof["session_id"],
    "profile_id": prof["profile_id"], "secret_handle": prof["secret"]["handle"],
    "provider": "openai-compatible-byok", "model": prof["model"],
    "message": "Build a 4-bit synchronous counter with enable and active-high reset.",
}
try:
    result = state.create_spec_session(payload)
    print("=== SPEC OK ===")
    print("agent_trace_id:", result.get("agent_trace_id"))
    props = result.get("state") or {}
    print("objective:", str(props.get("objective"))[:80])
    print("top:", props.get("top"), "| ready:", props.get("ready_for_execution"))
    print("rtl_source:", str(props.get("rtl_source"))[:200])
    tr = state.agent_traces.get(result.get("agent_trace_id", ""))
    if tr:
        for s in tr.steps:
            print(" trace step:", s.kind, "|", s.title[:36], "|", s.status, "|", (s.tool or ""), "|", s.duration_ms, "ms")
except Exception as exc:
    import traceback
    traceback.print_exc()
