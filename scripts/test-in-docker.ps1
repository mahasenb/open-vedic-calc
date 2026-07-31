<#
.SYNOPSIS
    Run the calc-service test suite inside Docker.
.DESCRIPTION
    Builds the test image (compiles pyswisseph from source and fetches the
    checksum-verified Swiss ephemeris data into it), and runs pytest.  Works
    on any OS with Docker, with nothing downloaded by hand beforehand.
.EXAMPLE
    .\scripts\test-in-docker.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

# No ephemeris precondition. The image build fetches and checksum-verifies the
# Swiss data itself (the `ephemeris` stage in Dockerfile.test), so this no
# longer depends on a human having downloaded ~2 MB of licensed binaries by
# hand first. The old check said "download Swiss Ephemeris files first" and
# named no source; skipping it ran the suite green on the Moshier fallback,
# because the accuracy tests SKIP rather than fail without the data. The build
# now fails closed on a download failure or a checksum mismatch instead.

Write-Host '=== Building test image (first run compiles pyswisseph, ~60s) ===' -ForegroundColor Cyan
docker compose -f (Join-Path $root 'docker-compose.test.yml') run --rm --build test
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host '=== All tests passed ===' -ForegroundColor Green
} else {
    Write-Host "=== Tests failed (exit code $exitCode) ===" -ForegroundColor Red
}

exit $exitCode
