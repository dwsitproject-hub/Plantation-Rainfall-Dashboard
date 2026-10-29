@echo off
cd /d "%~dp0"
python -m backend.run_dashboard
if errorlevel 1 pause
