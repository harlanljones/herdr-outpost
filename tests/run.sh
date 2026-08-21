#!/usr/bin/env bash
# ==============================================================================
# herdr-outpost Test Runner Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Running herdr-outpost test suite..."
echo "==> Working directory: ${REPO_ROOT}"

cd "${REPO_ROOT}"

if command -v uv >/dev/null 2>&1; then
    uv run --project relay --with pytest --with pytest-asyncio pytest tests/ -v "$@"
elif command -v pytest >/dev/null 2>&1; then
    pytest tests/ -v "$@"
else
    echo "ERROR: Neither 'uv' nor 'pytest' found in PATH. Please install uv or pytest." >&2
    exit 1
fi

echo "==> All tests completed successfully!"
