@echo off
chcp 65001 >nul
setlocal
set "PY={{PYTHON_EXE}}"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
"%PY%" selfcheck-taskboard.py %*
set "RC=%ERRORLEVEL%"
echo.
echo [taskboard] exit code = %RC%
pause
exit /b %RC%
