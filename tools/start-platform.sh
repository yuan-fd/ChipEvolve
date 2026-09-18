#!/usr/bin/env bash
set -euo pipefail

platform_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
platform_venv="${OPENROAD_PLATFORM_VENV:-$platform_root/.venv}"
state_root="${OPENROAD_PLATFORM_STATE_ROOT:-$platform_root/var}"
plugins_root="${OPENROAD_PLATFORM_PLUGINS_ROOT:-$platform_root/plugins}"
admissions_root="${OPENROAD_PLATFORM_ADMISSIONS_ROOT:-$platform_root/admissions}"
gateway_config="${OPENROAD_PLATFORM_GATEWAY_CONFIG:-$platform_root/examples/gateway-agent.json}"

mkdir -p "$state_root"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="127.0.0.1,localhost${no_proxy:+,$no_proxy}"

capacity_args=()
if [[ -n "${OPENROAD_PLATFORM_CAPACITY_CPU_CORES:-}" ]]; then
  capacity_args+=(--capacity-cpu-cores "$OPENROAD_PLATFORM_CAPACITY_CPU_CORES")
fi
if [[ -n "${OPENROAD_PLATFORM_CAPACITY_MEMORY_BYTES:-}" ]]; then
  capacity_args+=(--capacity-memory-bytes "$OPENROAD_PLATFORM_CAPACITY_MEMORY_BYTES")
fi

pids=()
cleanup() {
  trap - EXIT INT TERM
  if ((${#pids[@]})); then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"$platform_venv/bin/openroad-platform-worker" \
  --state-root "$state_root" --plugins-root "$plugins_root" \
  --admissions-root "$admissions_root" "${capacity_args[@]}" &
pids+=("$!")

"$platform_venv/bin/openroad-app-plan_executor" \
  --host 127.0.0.1 --port 8840 \
  --kernel-url http://127.0.0.1:8700 \
  --db "$state_root/plan_executor.sqlite" &
pids+=("$!")

"$platform_venv/bin/openroad-platform-gateway" \
  --host 127.0.0.1 --port 8700 --no-auth \
  --config "$gateway_config" --state-root "$state_root" \
  --plugins-root "$plugins_root" --admissions-root "$admissions_root" \
  "${capacity_args[@]}" &
pids+=("$!")

printf 'gateway: http://127.0.0.1:8700\nplan service: http://127.0.0.1:8840\n'
wait -n
