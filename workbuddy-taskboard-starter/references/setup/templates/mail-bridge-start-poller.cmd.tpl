@echo off
setlocal
set "PY={{PYTHON_EXE}}"
cd /d "%~dp0"
echo [mail-bridge] Starting IMAP poller. Press Ctrl+C to stop.
"%PY%" poller.py
