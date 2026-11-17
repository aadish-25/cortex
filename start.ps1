$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host " Starting Cortex Unified Development Environment..." -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

$pythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $pythonExe) {
    & $pythonExe run.py
} else {
    python run.py
}
