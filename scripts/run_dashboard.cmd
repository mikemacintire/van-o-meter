@echo off
rem Launched by the "EcoFlow Dashboard" scheduled task. Serves 127.0.0.1:8642.
rem Makes no cloud API calls unless a browser actually requests /api/live.
cd /d "%~dp0.."
if not exist logs mkdir logs
.venv\Scripts\python.exe -u dashboard.py > logs\dashboard.log 2>&1
