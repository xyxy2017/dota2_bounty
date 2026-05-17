param(
    [string]$TesseractDir = ""
)

$ErrorActionPreference = "Stop"

$BackendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $TesseractDir) {
    $TesseractDir = Join-Path $BackendDir "tools\tesseract"
}

$RequiredFiles = @(
    "tesseract.exe",
    "libtesseract-5.dll",
    "libleptonica-6.dll",
    "tessdata\eng.traineddata"
)

$OptionalFiles = @(
    "tessdata\chi_sim.traineddata"
)

$missing = @()
foreach ($relativePath in $RequiredFiles) {
    $path = Join-Path $TesseractDir $relativePath
    if (-not (Test-Path -LiteralPath $path)) {
        $missing += $relativePath
    }
}

Write-Host "Checking bundled Tesseract: $TesseractDir"
if ($missing.Count -gt 0) {
    Write-Host "Missing required files:"
    foreach ($item in $missing) {
        Write-Host "  - $item"
    }
    exit 1
}

Write-Host "Required OCR files found."

foreach ($relativePath in $OptionalFiles) {
    $path = Join-Path $TesseractDir $relativePath
    if (Test-Path -LiteralPath $path) {
        Write-Host "Optional file found: $relativePath"
    } else {
        Write-Host "Optional file missing: $relativePath"
    }
}
