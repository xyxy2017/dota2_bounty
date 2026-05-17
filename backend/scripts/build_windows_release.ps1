param(
    [string]$DistDir = "",
    [switch]$IncludeTesseract,
    [switch]$SkipTesseractCheck
)

$ErrorActionPreference = "Stop"

$BackendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$DefaultDistDir = Join-Path $BackendDir "dist"
if (-not $DistDir) {
    $DistDir = $DefaultDistDir
}
if ([System.IO.Path]::IsPathRooted($DistDir)) {
    $DistDir = [System.IO.Path]::GetFullPath($DistDir)
} else {
    $DistDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $DistDir))
}
$ReleaseDir = Join-Path $DistDir "Dota2BountyBackend"
$TesseractDir = Join-Path $BackendDir "tools\tesseract"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python venv not found: $Python. Run: python -m venv .venv; .\.venv\Scripts\pip install -e ."
}

if ($IncludeTesseract -and -not $SkipTesseractCheck) {
    & (Join-Path $PSScriptRoot "check_bundled_tesseract.ps1") -TesseractDir $TesseractDir
}

if (Test-Path -LiteralPath $ReleaseDir) {
    Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
}

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$null = & $Python -m pip show pyinstaller 2>$null
$pyInstallerMissing = $LASTEXITCODE -ne 0
$ErrorActionPreference = $previousErrorActionPreference
if ($pyInstallerMissing) {
    Write-Host "PyInstaller not found in backend venv. Installing pyinstaller..."
    & $Python -m pip install pyinstaller
}

Write-Host "Building Windows release into $ReleaseDir"
Push-Location $BackendDir
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --name Dota2BountyBackend `
        --onedir `
        --distpath $DistDir `
        --workpath (Join-Path $BackendDir "build\pyinstaller") `
        --specpath (Join-Path $BackendDir "build") `
        --paths "src" `
        "src\app\main.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
} finally {
    Pop-Location
}

$WebTarget = Join-Path $ReleaseDir "src\web"
New-Item -ItemType Directory -Path $WebTarget -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $BackendDir "src\web\*") -Destination $WebTarget -Recurse -Force

$RuntimeTarget = Join-Path $ReleaseDir "runtime"
New-Item -ItemType Directory -Path $RuntimeTarget -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $BackendDir "runtime\gamestate_integration_dota2_bounty.cfg.example") -Destination $RuntimeTarget -Force

if ($IncludeTesseract) {
    $TesseractTarget = Join-Path $ReleaseDir "tools\tesseract"
    New-Item -ItemType Directory -Path $TesseractTarget -Force | Out-Null
    Copy-Item -Path (Join-Path $TesseractDir "*") -Destination $TesseractTarget -Recurse -Force
}

$LauncherPath = Join-Path $ReleaseDir "start_dota2_bounty.ps1"
$Launcher = @'
param(
    [int]$Port = 8000,
    [switch]$AutoOcr,
    [switch]$AutoFocusDota,
    [switch]$OpenDashboard,
    [switch]$OpenOcrLive
)

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Exe = Join-Path $AppDir "Dota2BountyBackend.exe"

if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Dota2BountyBackend.exe not found next to launcher."
}

$env:DOTA2_BOUNTY_SERVER_PORT = [string]$Port
if ($AutoOcr) {
    $env:DOTA2_BOUNTY_AUTO_OCR_ENABLED = "1"
}
if ($AutoFocusDota) {
    $env:DOTA2_BOUNTY_AUTO_OCR_AUTO_FOCUS = "1"
} else {
    $env:DOTA2_BOUNTY_AUTO_OCR_AUTO_FOCUS = "0"
}

$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-Host "Stopping existing backend process on port $Port (PID $($conn.OwningProcess))"
    Stop-Process -Id $conn.OwningProcess -Force
    Start-Sleep -Seconds 1
}

$lockFile = Join-Path $AppDir "runtime\backend.lock"
if (Test-Path -LiteralPath $lockFile) {
    Remove-Item -LiteralPath $lockFile -Force
}

Write-Host "Starting Dota2 Bounty on http://127.0.0.1:$Port"
Start-Process -FilePath $Exe -WorkingDirectory $AppDir -WindowStyle Hidden
Start-Sleep -Seconds 3

$health = Invoke-RestMethod "http://127.0.0.1:$Port/health"
if (-not $health.ok) {
    throw "Backend health check failed"
}

if ($AutoOcr) {
    $autoOcrStatus = Invoke-RestMethod "http://127.0.0.1:$Port/debug/ocr/auto"
    Write-Host "Auto OCR tesseract source: $($autoOcrStatus.tesseract_source)"
    Write-Host "Auto OCR tesseract path: $($autoOcrStatus.tesseract_path)"
    if ($autoOcrStatus.needs_tesseract) {
        Write-Warning "Auto OCR text recognition is disabled because tesseract.exe was not found."
    }
}

if ($OpenDashboard) {
    Start-Process "http://127.0.0.1:$Port/"
}
if ($OpenOcrLive) {
    Start-Process "http://127.0.0.1:$Port/debug/ocr/live"
}
'@
[System.IO.File]::WriteAllText($LauncherPath, $Launcher, [System.Text.UTF8Encoding]::new($false))

Write-Host "Release build ready: $ReleaseDir"
Write-Host "Launcher: $LauncherPath"
