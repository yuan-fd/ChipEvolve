#!/usr/bin/env python3
"""Verify the real P8-Real Runtime database through HTTP API and web assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState, build_server  # noqa: E402


def _get(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read(), response.headers.get_content_type()


def _json(url: str) -> dict:
    body, _ = _get(url)
    return json.loads(body)


def _post(url: str) -> dict:
    request = urllib.request.Request(
        url, data=b"{}", method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--campaign-db", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime_db = args.runtime_db.expanduser().resolve()
    if not runtime_db.is_file() or not str(runtime_db).startswith("/tmp/"):
        raise ValueError("P8-Real live Runtime DB must exist under /tmp")
    local = Path("/tmp") / f"openroad-platform-p8-real-api-{uuid.uuid4().hex}"
    campaign_db = (args.campaign_db.expanduser().resolve() if args.campaign_db else
                   local / "campaign.db")
    state = ApiState(
        local / "platform.db", local / "uploads",
        ROOT / ".tools/taiwei-official-3d/orfs-research",
        design_root=local / "designs", legacy_root=local / "legacy",
        runtime_db_path=runtime_db, campaign_db_path=campaign_db,
    )
    run = state.runtime_store.get_run(args.run_id)
    campaign_id = state.campaign_store.create(
        "TaiWei gcd P8-Real acceptance", [run.task_spec], max_parallel=1,
        campaign_id="p8-real-gcd-acceptance",
    )
    member = state.campaign_store.members(campaign_id)[0]
    state.campaign_store.bind(member.member_id, args.run_id)

    server = build_server("127.0.0.1", 0, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        listing = _json(f"{base}/api/runtime/runs")
        detail = _json(f"{base}/api/runtime/runs/{args.run_id}")
        campaign = _json(f"{base}/api/campaigns/{campaign_id}")
        index, index_type = _get(f"{base}/")
        javascript, js_type = _get(f"{base}/assets/app.js")
        artifacts = detail["three_d"]["artifacts"]
        gds = next(item for item in artifacts if item["kind"] == "gds")
        view = next(item for item in artifacts if item["kind"] == "three_d_view")
        gds_body, gds_type = _get(f"{base}{gds['url']}")
        view_body, view_type = _get(f"{base}{view['url']}")
        cancel_result = _post(f"{base}/api/runtime/runs/{args.run_id}/cancel")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    checks = {
        "run_listed": any(item["run_id"] == args.run_id for item in listing["runs"]),
        "run_succeeded": detail["run"]["status"] == "succeeded",
        "replayable": detail["three_d"]["replayable"] is True,
        "upper_instances": detail["three_d"]["tiers"].get("upper_instances"),
        "bottom_instances": detail["three_d"]["tiers"].get("bottom_instances"),
        "hbt_metric_visible": "finish__route__hb_via__count__phys"
        in detail["three_d"]["metrics"],
        "cross_tier_metric_visible": "finish__route__cross_tier_nets__all"
        in detail["three_d"]["metrics"],
        "toolchain_visible": bool(detail["three_d"]["toolchain"].get("openroad_commit")),
        "campaign_bound": campaign["members"][0]["run_id"] == args.run_id,
        "gds_sha256": hashlib.sha256(gds_body).hexdigest(),
        "gds_sha_matches_runtime": hashlib.sha256(gds_body).hexdigest() == gds["sha256"],
        "gds_content_type": gds_type,
        "svg_visible": view_body.startswith(b"<svg"),
        "svg_content_type": view_type,
        "web_index_visible": b'id="view-three-d"' in index and index_type == "text/html",
        "web_javascript_visible": b"selectThreeDRun" in javascript
        and js_type in {"text/javascript", "application/javascript"},
        "terminal_cancel_is_idempotent": cancel_result["run"]["status"] == "succeeded",
    }
    required_true = (
        "run_listed", "run_succeeded", "replayable", "hbt_metric_visible",
        "cross_tier_metric_visible", "toolchain_visible", "campaign_bound",
        "gds_sha_matches_runtime", "svg_visible", "web_index_visible",
        "web_javascript_visible", "terminal_cancel_is_idempotent",
    )
    if not all(checks[name] for name in required_true):
        raise RuntimeError(f"P8-Real API/Web verification failed: {checks}")
    payload = {
        "schema_version": 1, "phase": "P8-Real-API-Web", "accepted": True,
        "run_id": args.run_id, "campaign_id": campaign_id,
        "runtime_db": str(runtime_db), "campaign_db": str(campaign_db),
        "server": "transient loopback HTTP server", "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
