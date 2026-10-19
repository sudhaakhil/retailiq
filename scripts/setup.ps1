# One-time setup. Run from anywhere:   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# Creates .venv, installs the pinned packages, creates the simulated database and runs the integrity checks.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Run($exe, $arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $exe $($arguments -join ' ')" }
}

Write-Host "== Python =="
Run "python" @("--version")
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "== Creating virtual environment .venv =="
    Run "python" @("-m", "venv", ".venv")
} else {
    Write-Host "== .venv already exists, reusing it =="
}
$py = ".\.venv\Scripts\python.exe"
Write-Host "== Installing packages (a few minutes the first time) =="
Run $py @("-m", "pip", "install", "--upgrade", "pip")
Run $py @("-m", "pip", "install", "-r", "requirements.txt")
Write-Host "== Creating the simulated database (about 15 seconds) =="
Run $py @("-m", "retailiq.cli", "init-db")
Write-Host "== Integrity checks =="
Run $py @("-m", "retailiq.cli", "check")
Write-Host ""
Write-Host "Setup complete. Start the app with:   powershell -ExecutionPolicy Bypass -File scripts\run.ps1"
