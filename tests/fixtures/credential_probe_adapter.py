#!/usr/bin/env python3
"""Reports presence only—never the credential value."""
import argparse, json, os
from datetime import datetime, timezone
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument("--request",type=Path);p.add_argument("--result",type=Path);a=p.parse_args()
now=datetime.now(timezone.utc).isoformat()
(a.result.parent/"report.json").write_text("credential presence recorded\n")
a.result.write_text(json.dumps({"schema_version":1,"status":"succeeded","exit_code":0,"started_at":now,"ended_at":now,"metrics":[{"name":"credential_seen","value":1 if os.environ.get("OPENROUTER_API_KEY") else 0}],"artifacts":[{"kind":"report","path":"report.json"}],"provenance":{}}))
