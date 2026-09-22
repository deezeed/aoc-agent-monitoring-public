# AOC bootstrap installer.
#
# This ships as part of the public aoc-agent-monitoring release repo (see
# export_public_release.ps1 in the private dev repo for how that release
# repo gets built) -- a plain `git clone` works with no credentials for
# anyone. If you're instead pairing a second machine you own to an
# existing private checkout, this still works the same way, just pass
# -InstallDir to control where it lands.
#
# Usage:
#   powershell -File install.ps1                    clone/update + confirm + apply
#   powershell -File install.ps1 -InstallDir D:\AOC  custom install location
#   powershell -File install.ps1 -DryRun             show what would happen, apply nothing

param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE "AOC"),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/deezeed/aoc-agent-monitoring-public.git"

Write-Host "AOC bootstrap installer"
Write-Host "Install dir: $InstallDir"
Write-Host ""

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Error "git is required but not found on PATH. Install Git for Windows first: https://git-scm.com/download/win"
}
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "python is required but not found on PATH. Install Python 3.9+ first: https://www.python.org/downloads/"
}

if (Test-Path $InstallDir) {
    Write-Host "Directory already exists -- pulling latest instead of cloning"
    Push-Location $InstallDir
    git pull
    Pop-Location
} else {
    git clone $RepoUrl $InstallDir
}

try {
    python -m pip install --quiet psutil
} catch {
    Write-Host "Warning: could not install psutil (optional -- only affects the /diag memory figure)"
}

Push-Location $InstallDir
Write-Host ""
Write-Host "--- dry run: showing exactly what setup.py would do ---"
python setup.py --dry-run
Write-Host ""

if ($DryRun) {
    Write-Host "Dry run only (-DryRun passed) -- stopping here. Re-run without -DryRun to apply."
    Pop-Location
    exit 0
}

$confirm = Read-Host "Apply these changes now? [y/N]"
if ($confirm -ne "y") {
    Write-Host "Aborted -- no changes made."
    Pop-Location
    exit 0
}

python setup.py
Pop-Location

Write-Host ""
Write-Host "Done. Dashboard: http://localhost:5151"
