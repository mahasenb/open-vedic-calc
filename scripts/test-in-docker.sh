#!/usr/bin/env bash
# Run the calc-service test suite inside Docker.
# Usage: bash scripts/test-in-docker.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Verify ephemeris data exists
if [ ! -f "$ROOT/data/ephe/sepl_18.se1" ]; then
    echo "ERROR: Ephemeris data not found at $ROOT/data/ephe/" >&2
    echo "       Download Swiss Ephemeris files first." >&2
    exit 1
fi

echo "=== Building test image (first run compiles pyswisseph, ~60s) ==="
docker compose -f "$ROOT/docker-compose.test.yml" run --rm --build test
rc=$?

if [ $rc -eq 0 ]; then
    echo "=== All tests passed ==="
else
    echo "=== Tests failed (exit code $rc) ==="
fi

exit $rc
