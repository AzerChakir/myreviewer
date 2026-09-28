# run_dashboard.ps1 — start the Angular dashboard (http://localhost:4200).
#
# Windows analogue of run_dashboard.sh. Installs frontend deps on the first run
# (locks a package-lock.json), then runs `npm start` (=> ng serve) which proxies
# /api to http://127.0.0.1:8000 (see dashboard/proxy.conf.json). Keep the
# backend running (run_api.ps1) so Analyze / Connect work end-to-end.
#
#   powershell -ExecutionPolicy Bypass -File .\run_dashboard.ps1
#   .\run_dashboard.bat      (thin shim for Explorer / double-click)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dashboard = Join-Path $root "dashboard"

if (-not (Test-Path (Join-Path $dashboard "package.json"))) {
    Write-Error "No dashboard\package.json — run this from prototype/."
    exit 1
}

# ── locate npm (mirror run_api.ps1's python search) ─────────────────────────
$npm = $null
$npx = $null
foreach ($d in @(
        (Join-Path $env:APPDATA "npm\npm.cmd"),
        (Join-Path $env:ProgramFiles "nodejs\npm.cmd"),
        (Join-Path $env:ProgramFiles "nodejs\npm"),
        (Join-Path $env:USERPROFILE ".local\bin\npm"),
        (Join-Path $env:LOCALAPPDATA "npm\npm.cmd")
    )) { if (Test-Path $d) { $npm = $d; $npx = $d -replace "npm", "npx"; break } }
if (-not $npm) {
    $cmd = Get-Command npm -ErrorAction SilentlyContinue
    if ($cmd) {
        $npm = $cmd.Source
        $npx = Join-Path (Split-Path $npm) "npx.cmd"
    } else {
        Write-Error "npm not found. Install Node.js >= 20.19 and ensure npm is on PATH."
        exit 1
    }
}
if (-not (Test-Path $npx)) { $npx = $npm }

Write-Host "npm        : $npm"
Write-Host "Dashboard  : $dashboard"

if (-not (Test-Path (Join-Path $dashboard "node_modules"))) {
    Write-Host "First run — installing frontend deps (this can take a minute)…"
    Push-Location $dashboard
    try { & $npm install --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw "npm install failed" } }
    finally { Pop-Location }
}

Write-Host ""
Write-Host "Serving dashboard at http://localhost:4200"
Write-Host "API proxy         -> http://127.0.0.1:8000  (start run_api.ps1 first)"
Write-Host "Connect GitHub    -> installs the App; webhook events arrive on :8000"
Write-Host "Ctrl+C to stop."
Write-Host ""
Push-Location $dashboard
try { & $npx ng serve --host 0.0.0.0 --port 4200 }
finally { Pop-Location }