# run_api.ps1 — start the PR-decomposer backend (FastAPI + GitHub App webhook).
#
# Windows analogue of run_api.sh. Detects a repo-local venv (recommended) or
# falls back to `python` on PATH. Serves on http://127.0.0.1:8000.
#
#   powershell -ExecutionPolicy Bypass -File .\run_api.ps1
#   .\run_api.bat            (thin .bat shim for Explorer / double-click)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── pick a python interpreter ────────────────────────────────────────────────
$candidates = @(
    (Join-Path $root ".venv\Scripts\python.exe"),     # prototype\.venv  (Windows)
    (Join-Path $root ".venv\bin\python"),             # prototype/.venv  (Linux venv on Windows)
    (Join-Path (Split-Path $root) ".venv\Scripts\python.exe")  # repo-root .venv
)
$python = $null
foreach ($p in $candidates) { if (Test-Path $p) { $python = $p; break } }
if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
    else {
        Write-Error "No Python found. Create a venv (python -m venv .venv) or ensure python is on PATH."
        exit 1
    }
}

# ── defaults (mirror run_api.sh) ─────────────────────────────────────────────
if (-not $env:DATA_DIR) { $env:DATA_DIR = Join-Path $root "data" }
if (-not $env:PYTHONUNBUFFERED) { $env:PYTHONUNBUFFERED = "1" }

Write-Host "API root : $root"
Write-Host "Python   : $python"
Write-Host "DATA_DIR : $env:DATA_DIR"
Write-Host "Serving  : http://127.0.0.1:8000   (Ctrl+C to stop)"

& $python -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload