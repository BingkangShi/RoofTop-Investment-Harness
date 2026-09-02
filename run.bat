@echo off
cd /d "%~dp0"
echo Starting RoofTop Investment Harness at http://127.0.0.1:8765
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app.server
) else (
  python -m app.server
)
