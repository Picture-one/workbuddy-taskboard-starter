@echo off
set "NODE_EXE={{NODE_EXE}}"
set "CODEX_TASKBOARD_URL=http://127.0.0.1:{{PORT}}"
"%NODE_EXE%" "{{APP_DIR}}\cli\taskctl.mjs" %*
