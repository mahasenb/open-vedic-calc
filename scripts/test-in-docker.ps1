<#
.SYNOPSIS
    Run the calc-service test suite inside Docker.
.DESCRIPTION
    Builds the test image (compiles pyswisseph from source), mounts the
    local ephemeris data, and runs pytest.  Works on any OS with Docker.
.EXAMPLE
    .\scripts\test-in-docker.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

# Verify ephemeris data exists
$ephePath = "$root\data\ephe"
if (-not (Test-Path (Join-Path $ephePath 'sepl_18.se1'))) {
    Write-Error "Ephemeris data not found at $ephePath - download Swiss Ephemeris files first."
    exit 1
}

Write-Host '=== Building test image (first run compiles pyswisseph, ~60s) ===' -ForegroundColor Cyan
docker compose -f (Join-Path $root 'docker-compose.test.yml') run --rm --build test
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host '=== All tests passed ===' -ForegroundColor Green
} else {
    Write-Host "=== Tests failed (exit code $exitCode) ===" -ForegroundColor Red
}

exit $exitCode
