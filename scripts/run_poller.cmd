@echo off
rem Launched by the "EcoFlow Poller" scheduled task. Truncates the log each
rem start so restarts do not compound it; the CSV is the durable record.
rem -u keeps stdout unbuffered so errors reach the log immediately.
cd /d "%~dp0.."
if not exist logs mkdir logs
.venv\Scripts\python.exe -u run_poller.py > logs\poller.log 2>&1
