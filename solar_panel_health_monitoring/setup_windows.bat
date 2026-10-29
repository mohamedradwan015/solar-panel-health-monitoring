@echo off
REM One-time setup on Windows (PowerShell or Command Prompt). Requires Python 3.11 or newer from python.org.
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 (echo Could not create a virtual environment. Is Python installed and on PATH? & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup finished. Start the app with run_windows.bat
