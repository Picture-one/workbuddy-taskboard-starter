@echo off
setlocal
set "NODE_EXE={{NODE_EXE}}"
set "APP_DIR={{APP_DIR}}"
set "CODEX_TASKBOARD_HOST=127.0.0.1"
set "CODEX_TASKBOARD_PORT={{PORT}}"
set "CODEX_TASKBOARD_DATA_DIR={{DATA_DIR}}"
set "CODEX_TASKBOARD_URL=http://127.0.0.1:{{PORT}}"
{{TRUSTED_ORIGINS_BLOCK}}

netstat -ano | findstr /C:"LISTENING" | findstr /C:":{{PORT}}" >nul 2>&1
if not errorlevel 1 (
  echo [taskboard] Port {{PORT}} is already in use - the service may already be running.
  echo [taskboard] Check with status-taskboard.cmd, or stop it with stop-taskboard.cmd.
  exit /b 1
)

echo [taskboard] Starting service at http://127.0.0.1:{{PORT}}
echo [taskboard] Data dir: %CODEX_TASKBOARD_DATA_DIR%
echo [taskboard] Press Ctrl+C to stop.
cd /d "%APP_DIR%"
"%NODE_EXE%" server\index.mjs
