@echo off
rem run_dashboard.bat — thin shim so you can double-click (Explorer) to start
rem the Angular dashboard, mirroring run_api.bat. The .ps1 does the real work
rem (npm detection + first-run install + `ng serve` on http://localhost:4200).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_dashboard.ps1"
if errorlevel 1 pause