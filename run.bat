@echo off
rem Fraudbusters: one click start for Windows.
rem First run creates a Python environment and installs packages (a few minutes), later runs start in seconds.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up Python environment, first run only...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Could not find Python. Install Python 3.11 or newer from python.org and tick "Add to PATH".
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist "data\transactions.csv" (
    ".venv\Scripts\python.exe" data\generate_synthetic.py
)

set PORT=8600
if not "%1"=="" set PORT=%1
echo.
echo Fraudbusters Workspace is starting on http://localhost:%PORT%
echo Close this window to stop it.
start "" "http://localhost:%PORT%"
".venv\Scripts\python.exe" -m uvicorn app.server:app --port %PORT%
pause
