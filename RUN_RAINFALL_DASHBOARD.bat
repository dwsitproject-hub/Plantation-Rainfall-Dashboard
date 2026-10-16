@echo off
cd /d "%~dp0"
python RUN_RAINFALL_DASHBOARD.py
if errorlevel 1 pause
