@echo off
setlocal enabledelayedexpansion
set "PORT={{PORT}}"
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:"LISTENING" ^| findstr /C:":%PORT%"') do (
  set "FOUND=1"
  echo [taskboard] Stopping PID %%p
  taskkill /PID %%p /F >nul 2>&1
)
if not defined FOUND echo [taskboard] Nothing is listening on port %PORT%.
echo [taskboard] Done.
