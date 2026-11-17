@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo  Running Cortex Test Suite...
echo ========================================================

if "%~1"=="" (
    set "TARGETS=tests/unit tests/integration"
) else (
    set "TARGETS=%*"
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m pytest %TARGETS%
) else (
    python -m pytest %TARGETS%
)
