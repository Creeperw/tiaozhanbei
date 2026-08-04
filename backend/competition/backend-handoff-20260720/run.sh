#!/usr/bin/env bash
set -euo pipefail
SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_ROOT/../../platform_backend/run.sh" "$@"
