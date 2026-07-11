#!/usr/bin/env bash
# Run pytest using the local venv if present, exporting PYTHONPATH so `backend` imports resolve.
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$REPO_ROOT"
if [ -x "$REPO_ROOT/venv/bin/python" ]; then
  "$REPO_ROOT/venv/bin/python" -m pytest tests/tests_for_integration_branch -q
else
  python -m pytest tests/tests_for_integration_branch -q
fi
