@echo off
set "NODE_EXE={{NODE_EXE}}"
"%NODE_EXE%" -e "fetch('http://127.0.0.1:{{PORT}}/health').then(r=>{console.log('[taskboard] ONLINE  HTTP '+r.status);return r.text()}).then(t=>console.log('[taskboard] '+t)).catch(()=>{console.log('[taskboard] OFFLINE - run start-taskboard.cmd first.');process.exit(1)})"
