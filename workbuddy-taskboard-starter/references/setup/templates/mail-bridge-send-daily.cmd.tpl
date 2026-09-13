@echo off
setlocal
set "PY={{PYTHON_EXE}}"
cd /d "%~dp0"
"%PY%" mailer.py
