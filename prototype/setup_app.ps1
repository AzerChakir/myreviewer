# setup_app.ps1 — one-time provisioning for the GitHub App (Windows).
#
# Two jobs only, both safe & re-runnable:
#   [1] make sure a private key exists  -> prototype/private-key.pem
#   [2] print the exact 4 lines to add to prototype/.env (App credentials)
#
# Matches GUIDE.md "GitHub App → configure now". The dashboard's "Connect
# GitHub" button and the webhook are already coded; this just wires the
# credentials that make the App real. Nothing leaves this machine.
#
#   powershell -ExecutionPolicy Bypass -File .\setup_app.ps1
#   .\setup_app.bat            (thin shim for Explorer / double-click)

$ErrorActionPreference = "Stop"
$proto = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $proto ".env"
$pemPath = Join-Path $proto "private-key.pem"
$existingKeys = @()

# ── find openssl (Git for Windows, Anaconda, OpenSSL-Win64, PATH) ────────────
$openssl = $null
foreach ($cand in @(
        (Join-Path $env:ProgramFiles "Git\usr\bin\openssl.exe"),
        (Join-Path $env:ProgramFiles "Git\mingw64\bin\openssl.exe"),
        (Join-Path $env:ProgramFiles "OpenSSL-Win64\bin\openssl.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Git\usr\bin\openssl.exe"),
        (Join-Path $env:ProgramData "anaconda3\Library\bin\openssl.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\Library\bin\openssl.exe"),
    )) { if (Test-Path $cand) { $openssl = $cand; break } }
if (-not $openssl) {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd) { $openssl = $cmd.Source }
}

Write-Host ""
Write-Host "== GitHub App provisioning (Windows) =="
Write-Host ("   prototype : $proto")
Write-Host ("   key file  : $pemPath")

# ── [1] private key ───────────────────────────────────────────────────────────
if (Test-Path $pemPath) {
    Write-Host ""
    Write-Host ("[1/2] private key already present  ({0} bytes). Keeping it." -f (Get-Item $pemPath).Length)
} elseif ($openssl) {
    Write-Host ""
    Write-Host "[1/2] generating private key with openssl…"
    Write-Host ("      using : $openssl")
    & $openssl genrsa -out $pemPath 3072 2>$null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pemPath)) {
        Write-Error "openssl genrsa failed. Check openssl and re-run."
    }
    Write-Host ("      wrote : $pemPath  ({0} bytes)" -f (Get-Item $pemPath).Length)
} else {
    Write-Host ""
    Write-Host "[1/2] openssl not found and no key yet."
    Write-Host "      Install Git for Windows (bundles openssl at "
    Write-Host "      'C:\Program Files\Git\usr\bin\openssl.exe') or Anaconda,"
    Write-Host "      then re-run. Alternatively create private-key.pem yourself"
    Write-Host "      (RSA 3072, PKCS#1) and point GITHUB_PRIVATE_KEY_PATH at it."
    exit 1
}

# ── [2] which .env values are already set? ────────────────────────────────────
function Get-Key([string]$key) {
    if (-not (Test-Path $envPath)) { return "" }
    $m = Select-String -Path $envPath -Pattern ("^" + [regex]::Escape($key) + "\s*=\s*(.*?)\s*$")
    if (-not $m) { return "" }
    return $m.Matches[0].Groups[1].Value.TrimEnd()
}
$appId    = Get-Key "GITHUB_APP_ID"
$enabled  = Get-Key "GITHUB_APP_ENABLED"
$secret   = Get-Key "GITHUB_WEBHOOK_SECRET"
$keyPathEnv = Get-Key "GITHUB_PRIVATE_KEY_PATH"

Write-Host ""
Write-Host "[2/2] .env App settings — copy these into prototype/.env:"
Write-Host ""
Write-Host "    # ── GitHub App (phase-2 webhook + dashboard) ─────────────"
Write-Host ("    GITHUB_APP_ENABLED=1" + $(if ($enabled -in @("1","true")) {"    # already set"} else {"    # <-- flip to 1"}))
Write-Host ("    GITHUB_APP_ID=$appId" + $(if ($appId) {"    # already set"} else {"    # numeric id from your App's About page"}))
if ($secret) { Write-Host "    GITHUB_WEBHOOK_SECRET=$secret    # already set" }
else {
    $s = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    Write-Host "    GITHUB_WEBHOOK_SECRET=$s    # your webhook secret (any long random string)"
}
if ($keyPathEnv) { Write-Host ("    GITHUB_PRIVATE_KEY_PATH=$keyPathEnv    # already set") }
else { Write-Host "    GITHUB_PRIVATE_KEY_PATH=$pemPath    # path to the .pem above" }
Write-Host ""
Write-Host "    # optional – only if you don't want the slug auto-fetched:"
Write-Host "    GITHUB_APP_SLUG=<your-app-slug>"
Write-Host ""
Write-Host "By default the token cache (data/app_tokens.json) and installations"
Write-Host "(data/installations.json) are stored under prototype/data/."
Write-Host ""
Write-Host "Next steps (per GUIDE.md):"
Write-Host "   1. Create the App at https://github.com/settings/apps/new"
Write-Host "      -> hook private-key.pem + GITHUB_APP_ID + webhook secret in .env."
Write-Host "   2. Point the App's webhook URL at {public}/webhook (temp tunnel ok)."
Write-Host "   3. .\run_api.ps1  +  .\run_dashboard.ps1  -> Connect GitHub."
Write-Host ""