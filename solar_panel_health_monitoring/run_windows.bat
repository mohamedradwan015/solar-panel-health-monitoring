@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
set HOST=127.0.0.1
set PORT=8000
echo Opening http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
python app.py
