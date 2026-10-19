# Start RetailIQ.   powershell -ExecutionPolicy Bypass -File scripts\run.ps1
# Opens http://127.0.0.1:8501 - press Ctrl+C in this window to stop.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "No .venv found. Run scripts\setup.ps1 first." }
& .\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
