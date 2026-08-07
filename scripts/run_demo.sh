#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PACKAGE_PATHS="$PLATFORM_ROOT/packages/contracts/src:$PLATFORM_ROOT/packages/execution/src:$PLATFORM_ROOT/packages/scheduler/src:$PLATFORM_ROOT/packages/analysis/src:$PLATFORM_ROOT/packages/visualization/src"

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$PACKAGE_PATHS:$PYTHONPATH"
else
  export PYTHONPATH="$PACKAGE_PATHS"
fi

DEMO_HOST="${HOST:-0.0.0.0}"
DEMO_PORT="${PORT:-8000}"
DEMO_ORFS_ROOT="${ORFS_ROOT:-$PLATFORM_ROOT/../OpenROAD-flow-scripts}"
DEMO_OPENROAD_BIN="${OPENROAD_BIN:-$PLATFORM_ROOT/../bin/openroad}"
DEMO_YOSYS_BIN="${YOSYS_BIN:-$PLATFORM_ROOT/../bin/yosys}"
DEMO_DB="$PLATFORM_ROOT/var/platform.db"

for required_path in "$DEMO_ORFS_ROOT" "$DEMO_OPENROAD_BIN" "$DEMO_YOSYS_BIN"; do
  if [[ ! -e "$required_path" ]]; then
    echo "Required tool path is missing: $required_path" >&2
    exit 1
  fi
done

cd "$PLATFORM_ROOT"
python3 scripts/run_runtime_worker.py --db "$DEMO_DB" \
  --orfs-root "$DEMO_ORFS_ROOT" &
WORKER_PID=$!

cleanup() {
  if kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID"
    wait "$WORKER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "OpenROAD Platform is starting on http://$DEMO_HOST:$DEMO_PORT"
echo "For remote access, tunnel port $DEMO_PORT and open http://127.0.0.1:$DEMO_PORT"
python3 apps/api/app.py --host "$DEMO_HOST" --port "$DEMO_PORT" --db "$DEMO_DB" \
  --orfs-root "$DEMO_ORFS_ROOT"
