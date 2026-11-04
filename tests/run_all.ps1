# Runs every test in tests/js and tests/python, aggregates the result.
# Usage: powershell -File tests/run_all.ps1 [-Jobs N] [-Serial]
#
# Test files run in parallel (-Jobs at once, default = CPU count) -- each is
# its own process and they share nothing: servers bind port 0, scratch data
# goes to temp dirs. Output is buffered per file and printed in the usual
# order once all are done, so a log reads exactly like a serial run.
# -Serial (or -Jobs 1) streams each file as it runs, the old behavior.
param(
    [int]$Jobs = [Environment]::ProcessorCount,
    [switch]$Serial
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Serial -or $Jobs -lt 1) { $Jobs = 1 }

$tests = @()
$tests += Get-ChildItem -Path (Join-Path $root "js") -Filter "*.test.js" | Sort-Object Name |
    ForEach-Object { [pscustomobject]@{ Group = "JS"; Name = $_.Name; Exe = "node"; Path = $_.FullName } }
$tests += Get-ChildItem -Path (Join-Path $root "python") -Filter "*.test.py" | Sort-Object Name |
    ForEach-Object { [pscustomobject]@{ Group = "Python"; Name = $_.Name; Exe = "python"; Path = $_.FullName } }

$failed = @()
$started = Get-Date

if ($Jobs -eq 1) {
    $group = ""
    foreach ($t in $tests) {
        if ($t.Group -ne $group) { $group = $t.Group; Write-Host "`n=== $group tests ===" }
        Write-Host "`n--- $($t.Name) ---"
        & $t.Exe $t.Path
        if ($LASTEXITCODE -ne 0) { $failed += $t.Name }
    }
} else {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("aoc_tests_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $running = @{}   # index -> Process
    $exit = @{}      # index -> exit code
    $next = 0
    while ($next -lt $tests.Count -or $running.Count) {
        while ($running.Count -lt $Jobs -and $next -lt $tests.Count) {
            $t = $tests[$next]
            # Start-Process can't merge stdout+stderr into one file, so cmd does
            # it; PYTHONIOENCODING keeps non-ASCII check labels intact in the file
            $out = Join-Path $tmp "$next.txt"
            $env:PYTHONIOENCODING = "utf-8"
            $p = Start-Process -FilePath "cmd.exe" -NoNewWindow -PassThru `
                -ArgumentList "/d /c `"$($t.Exe) `"$($t.Path)`" > `"$out`" 2>&1`""
            # PowerShell 5.1 quirk: ExitCode stays empty after exit unless the
            # process handle was opened while it was still running
            $null = $p.Handle
            $running[$next] = $p
            $next++
        }
        Start-Sleep -Milliseconds 100
        foreach ($i in @($running.Keys)) {
            if ($running[$i].HasExited) {
                $running[$i].WaitForExit()
                $exit[$i] = $running[$i].ExitCode
                $running.Remove($i)
            }
        }
    }
    $group = ""
    for ($i = 0; $i -lt $tests.Count; $i++) {
        $t = $tests[$i]
        if ($t.Group -ne $group) { $group = $t.Group; Write-Host "`n=== $group tests ===" }
        Write-Host "`n--- $($t.Name) ---"
        $file = Join-Path $tmp "$i.txt"
        if (Test-Path $file) { Get-Content -Path $file -Encoding UTF8 | ForEach-Object { Write-Host $_ } }
        if ($exit[$i] -ne 0) { $failed += $t.Name }
    }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host "`n=== Summary ==="
Write-Host ("{0} test files, {1} at a time, {2:N0} s" -f $tests.Count, $Jobs, ((Get-Date) - $started).TotalSeconds)
if ($failed.Count -eq 0) {
    Write-Host "All test files passed."
    exit 0
} else {
    Write-Host "FAILED: $($failed -join ', ')"
    exit 1
}
