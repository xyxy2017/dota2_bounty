param(
    [int]$Port = 8000,
    [switch]$OpenDashboard,
    [switch]$OpenOcrLive,
    [switch]$AutoOcr,
    [switch]$AutoFocusDota,
    [switch]$AutoOcrDebugImages,
    [string]$TesseractPath = "",
    [string]$TesseractLang = "",
    [int]$TesseractPsm = 0,
    [int]$TesseractOem = -1,
    [int]$AutoOcrImageScale = 0,
    [int]$AutoOcrImageThreshold = -1,
    [int]$AutoOcrConfirmScans = 0,
    [int]$AutoOcrCooldownSeconds = 0,
    [int]$AutoOcrDebugMaxRuns = 0
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
if ($AutoFocusDota) {
    $env:DOTA2_BOUNTY_AUTO_OCR_AUTO_FOCUS = "1"
} else {
    $env:DOTA2_BOUNTY_AUTO_OCR_AUTO_FOCUS = "0"
}
if ($AutoOcrDebugImages) {
    $env:DOTA2_BOUNTY_AUTO_OCR_DEBUG_IMAGES = "1"
} else {
    $env:DOTA2_BOUNTY_AUTO_OCR_DEBUG_IMAGES = "0"
}
if ($TesseractPath) {
    $env:DOTA2_BOUNTY_TESSERACT_PATH = $TesseractPath
}
if ($TesseractLang) {
    $env:DOTA2_BOUNTY_TESSERACT_LANG = $TesseractLang
}
if ($TesseractPsm -gt 0) {
    $env:DOTA2_BOUNTY_TESSERACT_PSM = [string]$TesseractPsm
}
if ($TesseractOem -ge 0) {
    $env:DOTA2_BOUNTY_TESSERACT_OEM = [string]$TesseractOem
}
if ($AutoOcrImageScale -gt 0) {
    $env:DOTA2_BOUNTY_AUTO_OCR_IMAGE_SCALE = [string]$AutoOcrImageScale
}
if ($AutoOcrImageThreshold -ge 0) {
    $env:DOTA2_BOUNTY_AUTO_OCR_IMAGE_THRESHOLD = [string]$AutoOcrImageThreshold
}
if ($AutoOcrConfirmScans -gt 0) {
    $env:DOTA2_BOUNTY_AUTO_OCR_CONFIRM_SCANS = [string]$AutoOcrConfirmScans
}
if ($AutoOcrCooldownSeconds -gt 0) {
    $env:DOTA2_BOUNTY_AUTO_OCR_COOLDOWN_SECONDS = [string]$AutoOcrCooldownSeconds
}
if ($AutoOcrDebugMaxRuns -gt 0) {
    $env:DOTA2_BOUNTY_AUTO_OCR_DEBUG_MAX_RUNS = [string]$AutoOcrDebugMaxRuns
}

Write-Host "Starting Dota2 Bounty backend on http://127.0.0.1:$Port"
Start-Process -FilePath $Python -ArgumentList "-m", "app.main" -WorkingDirectory $BackendDir -WindowStyle Hidden
Start-Sleep -Seconds 3

$health = Invoke-RestMethod "http://127.0.0.1:$Port/health"
if (-not $health.ok) {
    throw "Backend health check failed"
}

Write-Host "Backend is healthy."

if ($AutoOcr) {
    try {
        $autoOcrStatus = Invoke-RestMethod "http://127.0.0.1:$Port/debug/ocr/auto"
        Write-Host "Auto OCR is enabled."
        Write-Host "  process: $($autoOcrStatus.process_name)"
        Write-Host "  focus mode: $($autoOcrStatus.auto_focus)"
        Write-Host "  tesseract source: $($autoOcrStatus.tesseract_source)"
        Write-Host "  tesseract path: $($autoOcrStatus.tesseract_path)"
        Write-Host "  OCR language: $($autoOcrStatus.tesseract_lang)"
        Write-Host "  confirm scans: $($autoOcrStatus.confirm_scans)"
        Write-Host "  cooldown seconds: $($autoOcrStatus.cooldown_seconds)"
        Write-Host "  debug images: $($autoOcrStatus.debug_images)"
        Write-Host "  debug dir: $($autoOcrStatus.debug_dir)"
        if ($autoOcrStatus.needs_tesseract) {
            Write-Warning "Auto OCR can capture images, but text recognition is disabled because tesseract.exe was not found."
            Write-Warning "For release builds, place the OCR runtime under backend\tools\tesseract or pass -TesseractPath."
        }
        if ($autoOcrStatus.tesseract_missing_langs -and $autoOcrStatus.tesseract_missing_langs.Count -gt 0) {
            Write-Warning "Missing requested OCR language data: $($autoOcrStatus.tesseract_missing_langs -join ', ')"
        }
    } catch {
        Write-Warning "Auto OCR status check failed: $($_.Exception.Message)"
    }
}

try {
    $gsiProbeStatus = Invoke-RestMethod "http://127.0.0.1:$Port/debug/gsi/id-probe"
    Write-Host "GSI ID probe is enabled: $($gsiProbeStatus.enabled)"
    Write-Host "  probe page: http://127.0.0.1:$Port/debug/gsi/id-probe-ui"
    Write-Host "  raw payload dir: $($gsiProbeStatus.raw_payload_dir)"
} catch {
    Write-Warning "GSI ID probe status check failed: $($_.Exception.Message)"
}

if ($OpenDashboard) {
    Start-Process "http://127.0.0.1:$Port/"
}

if ($OpenOcrLive) {
    Start-Process "http://127.0.0.1:$Port/debug/ocr/live"
}
