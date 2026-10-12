#!/usr/bin/env bash
# Test the installed checkout, independently of the caller's working directory.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONDONTWRITEBYTECODE=1
export BRIDGE_HOST_TESTS=1
# Tests must never write production memory; memory tests use isolated stores.
export BRIDGE_MEMORY_DISABLED=1
export BRIDGE_OPERATIONS_DIR
BRIDGE_OPERATIONS_DIR=$(mktemp -d /tmp/bridge-example.XXXXXX)
trap 'rm -rf -- "$BRIDGE_OPERATIONS_DIR"' EXIT
if [[ ! -x .venv/bin/python ]]; then
  echo 'Missing .venv/bin/python; create the Bridge environment from requirements.lock.' >&2
  exit 1
fi
.venv/bin/python -B -c 'import fastapi, mcp'
.venv/bin/python -B -m unittest discover -q
# GI belongs to system Python; do not silently skip the worker tests.
/usr/bin/python3 -B -c 'import gi'
/usr/bin/python3 -B -m unittest -q test_desktop test_desktop_semantics test_browser_pointer_worker test_portal_persistence
