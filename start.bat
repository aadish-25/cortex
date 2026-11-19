@echo off
if "%~1"=="__run" goto :run
cmd /c "%~f0" __run <nul
exit /b %errorlevel%

:run
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
exit /b
