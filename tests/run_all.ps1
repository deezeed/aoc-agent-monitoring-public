# Runs every test in tests/js and tests/python, aggregates the result.
# Usage: powershell -File tests/run_all.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$failed = @()

Write-Host "=== JS tests ==="
Get-ChildItem -Path (Join-Path $root "js") -Filter "*.test.js" | ForEach-Object {
    Write-Host "`n--- $($_.Name) ---"
    node $_.FullName
    if ($LASTEXITCODE -ne 0) { $failed += $_.Name }
}

Write-Host "`n=== Python tests ==="
Get-ChildItem -Path (Join-Path $root "python") -Filter "*.test.py" | ForEach-Object {
    Write-Host "`n--- $($_.Name) ---"
    python $_.FullName
    if ($LASTEXITCODE -ne 0) { $failed += $_.Name }
}

Write-Host "`n=== Summary ==="
if ($failed.Count -eq 0) {
    Write-Host "All test files passed."
    exit 0
} else {
    Write-Host "FAILED: $($failed -join ', ')"
    exit 1
}
