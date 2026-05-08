param(
    [int]$Port = 8000,
    [switch]$OpenDashboard,
    [switch]$OpenOcrLive,
    [switch]$AutoOcr,
    [string]$TesseractPath = ""
)

$ErrorActionPreference = "Stop"

$BackendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LockFile = Join-Path $BackendDir "runtime\backend.lock"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python venv not found: $Python. Run: python -m venv .venv; .\.venv\Scripts\pip install -e ."
}

$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-Host "Stopping existing backend process on port $Port (PID $($conn.OwningProcess))"
    Stop-Process -Id $conn.OwningProcess -Force
    Start-Sleep -Seconds 1
}

if (Test-Path -LiteralPath $LockFile) {
    Write-Host "Removing stale lock: $LockFile"
    Remove-Item -LiteralPath $LockFile -Force
}

$env:DOTA2_BOUNTY_SERVER_PORT = [string]$Port
$env:PYTHONPATH = "src"
if ($AutoOcr) {
    $env:DOTA2_BOUNTY_AUTO_OCR_ENABLED = "1"
}
if ($TesseractPath) {
    $env:DOTA2_BOUNTY_TESSERACT_PATH = $TesseractPath
}

Write-Host "Starting Dota2 Bounty backend on http://127.0.0.1:$Port"
Start-Process -FilePath $Python -ArgumentList "-m", "app.main" -WorkingDirectory $BackendDir -WindowStyle Hidden
Start-Sleep -Seconds 3

$health = Invoke-RestMethod "http://127.0.0.1:$Port/health"
if (-not $health.ok) {
    throw "Backend health check failed"
}

Write-Host "Backend is healthy."

if ($OpenDashboard) {
    Start-Process "http://127.0.0.1:$Port/"
}

if ($OpenOcrLive) {
    Start-Process "http://127.0.0.1:$Port/debug/ocr/live"
}
