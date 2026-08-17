#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-3: minimal MCP-style server exposing platform concepts to AI agents.

Implements the Model Context Protocol JSON-RPC surface (initialize /
tools/list / tools/call) over HTTP so Si2-aligned AI agents can discover and
query platform concepts and data. Standalone on port 8200.

Tools exposed:
  list_observations  - list learning observations (filterable)
  export_si2         - observations exported in Si2-style structure
  get_term_map       - platform term -> Si2 concept mapping
"""
import json
import sys
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/contracts/src", ROOT / "packages/analysis/src"):
    sys.path.insert(0, str(source))

from openroad_platform_analysis import TenantLearningStore  # noqa: E402

OWNER = "user-ea0c3d1f4520448d99a22d2dc7f7b250"  # yuanwenjie
PROJECT = "openroad-platform"

TOOLS = [
    {"name": "list_observations", "description": "List learning observations",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "export_si2", "description": "Observations in Si2-style structure",
     "inputSchema": {"type": "object"}},
    {"name": "get_term_map", "description": "Platform term to Si2 concept mapping",
     "inputSchema": {"type": "object"}},
]


class State:
    def __init__(self):
        store_path = ROOT / "var" / "public" / "tenant-learning.db"
        self.store = TenantLearningStore(store_path)
        self.term_map = json.loads(
            (ROOT / "knowledge" / "si2_term_map.json").read_text(encoding="utf-8"))

    def list_observations(self, limit: int = 50):
        return [o.to_dict() for o in self.store.list(OWNER, PROJECT)][:limit]

    def export_si2(self):
        records = []
        for obs in self.store.list(OWNER, PROJECT):
            ctx, metrics = obs.context, obs.metrics or {}
            records.append({
                "record_type": "si2_ai_eda_observation_v1",
                "record_id": obs.observation_id,
                "design": {"design_id": ctx.design_id,
                           "design_fingerprint": ctx.design_fingerprint},
                "flow": {"flow_stage": ctx.flow_stage},
                "pdk_library": {"pdk_id": ctx.pdk_id, "platform": ctx.platform,
                                "toolchain_id": ctx.toolchain_id},
                "timing": {"wns_ns": metrics.get("setup_wns_ns")},
                "verification": {"drc_errors": metrics.get("drc_errors")},
                "source": "observed",
            })
        return records

    def get_term_map(self):
        return {"standard": self.term_map["standard"],
                "mapping_count": len(self.term_map["mapping"]),
                "mapping": self.term_map["mapping"]}


STATE = State()


class Handler(BaseHTTPRequestHandler):
    def _reply(self, payload: dict, code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/mcp"):
            self._reply({"service": "platform-mcp", "protocol": "MCP (JSON-RPC over HTTP)",
                         "tools": [t["name"] for t in TOOLS]})
        elif self.path == "/health":
            self._reply({"ok": True})
        else:
            self._reply({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/mcp":
            return self._reply({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            return self._reply({"jsonrpc": "2.0", "error": {"code": -32700,
                               "message": f"parse error: {exc}"}})
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}
        if method == "initialize":
            return self._reply({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "openroad-platform-mcp", "version": "0.1.0"}}})
        if method == "tools/list":
            return self._reply({"jsonrpc": "2.0", "id": rid,
                                "result": {"tools": TOOLS}})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                if name == "list_observations":
                    result = STATE.list_observations(int(args.get("limit", 50)))
                elif name == "export_si2":
                    result = STATE.export_si2()
                elif name == "get_term_map":
                    result = STATE.get_term_map()
                else:
                    return self._reply({"jsonrpc": "2.0", "id": rid,
                                        "error": {"code": -32601,
                                                  "message": f"unknown tool {name}"}})
                return self._reply({"jsonrpc": "2.0", "id": rid,
                                    "result": {"content": [
                                        {"type": "text",
                                         "text": json.dumps(result, ensure_ascii=False)}]}})
            except Exception as exc:
                return self._reply({"jsonrpc": "2.0", "id": rid,
                                    "error": {"code": -32000, "message": str(exc)}})
        return self._reply({"jsonrpc": "2.0", "id": rid,
                            "error": {"code": -32601, "message": f"unknown method {method}"}})

    def log_message(self, fmt, *args):
        sys.stderr.write("[mcp] %s\n" % (fmt % args))


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8200
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"platform MCP server on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
