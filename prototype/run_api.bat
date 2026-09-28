@echo off
rem run_api.bat — thin shim so you can double-click (Explorer) to start the
rem backend, mirroring run_api.ps1. The .ps1 does all the real work.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_api.ps1"
if errorlevel 1 pause