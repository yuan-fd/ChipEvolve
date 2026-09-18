#!/usr/bin/env bash
set -euo pipefail

platform_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
platform_venv="${OPENROAD_PLATFORM_VENV:-$platform_root/.venv}"

python3 -m venv "$platform_venv"
# Python 3.12 venvs no longer guarantee setuptools.  The editable package
# builds use a setuptools backend, so bootstrap only the build tools inside the
# project venv before installing platform packages; the host Python is untouched.
"$platform_venv/bin/python" -m pip install --disable-pip-version-check \
  --upgrade setuptools wheel
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
