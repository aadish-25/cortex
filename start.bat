@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo  Starting Cortex Unified Development Environment...
echo ========================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py
) else (
    python run.py
)
