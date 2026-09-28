@echo off
rem setup_app.bat — thin shim so you can double-click (Explorer) to run
rem setup_app.ps1, mirroring run_api.bat / run_dashboard.bat. The .ps1 does the
rem real work (openssl keygen + .env coaching + open-ended App setup).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_app.ps1"
if errorlevel 1 pause