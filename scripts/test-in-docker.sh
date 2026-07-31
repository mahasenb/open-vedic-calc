#!/usr/bin/env bash
# Run the calc-service test suite inside Docker.
# Usage: bash scripts/test-in-docker.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# No ephemeris precondition. The image build fetches and checksum-verifies the
# Swiss data itself (the `ephemeris` stage in Dockerfile.test), so this no
# longer depends on a human having downloaded ~2 MB of licensed binaries by
# hand first. The old check said "Download Swiss Ephemeris files first" and
# named no source; skipping it ran the suite green on the Moshier fallback,
# because the accuracy tests SKIP rather than fail without the data. The build
# now fails closed on a download failure or a checksum mismatch instead.

echo "=== Building test image (first run compiles pyswisseph, ~60s) ==="
docker compose -f "$ROOT/docker-compose.test.yml" run --rm --build test
rc=$?

if [ $rc -eq 0 ]; then
    echo "=== All tests passed ==="
else
    echo "=== Tests failed (exit code $rc) ==="
fi

exit $rc
