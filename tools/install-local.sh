#!/usr/bin/env bash
set -euo pipefail

platform_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
platform_venv="${OPENROAD_PLATFORM_VENV:-$platform_root/.venv}"

python3 -m venv "$platform_venv"
"$platform_venv/bin/python" -m pip install --no-deps --no-build-isolation \
  -e "$platform_root/contracts" \
  -e "$platform_root/core/runtime" \
  -e "$platform_root/core/registry" \
  -e "$platform_root/core/evaluator" \
  -e "$platform_root/core/provenance" \
  -e "$platform_root/core/identity" \
  -e "$platform_root/core/client" \
  -e "$platform_root/gateway" \
  -e "$platform_root/apps/plan_executor"

printf 'installed in %s\n' "$platform_venv"
