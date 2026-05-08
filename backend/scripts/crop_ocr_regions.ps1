param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ImagePath)) {
    throw "Image not found: $ImagePath"
}

$imageFullPath = (Resolve-Path -LiteralPath $ImagePath).Path
$backendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $OutputDir) {
    $OutputDir = Join-Path $backendDir "runtime\ocr-crop-tests"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Add-Type -AssemblyName System.Drawing

$image = [System.Drawing.Image]::FromFile($imageFullPath)
try {
    $presets = @(
        @{ Name = "draft_top_full"; X = 10; Y = 7; W = 80; H = 4 },
        @{ Name = "draft_top_tight"; X = 10; Y = 8; W = 80; H = 3 },
        @{ Name = "draft_top_left5"; X = 10; Y = 7; W = 33; H = 4 },
        @{ Name = "draft_top_right5"; X = 57; Y = 7; W = 33; H = 4 },
        @{ Name = "draft_top_high"; X = 10; Y = 6; W = 80; H = 4 },
        @{ Name = "draft_top_low"; X = 10; Y = 9; W = 80; H = 4 }
    )

    foreach ($preset in $presets) {
        $x = [Math]::Floor($image.Width * $preset.X / 100)
        $y = [Math]::Floor($image.Height * $preset.Y / 100)
        $w = [Math]::Min($image.Width - $x, [Math]::Floor($image.Width * $preset.W / 100))
        $h = [Math]::Min($image.Height - $y, [Math]::Floor($image.Height * $preset.H / 100))
        $rect = New-Object System.Drawing.Rectangle($x, $y, $w, $h)
        $bitmap = New-Object System.Drawing.Bitmap($w, $h)
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.DrawImage($image, 0, 0, $rect, [System.Drawing.GraphicsUnit]::Pixel)
            } finally {
                $graphics.Dispose()
            }
            $outPath = Join-Path $OutputDir "$($preset.Name)_x$($preset.X)_y$($preset.Y)_w$($preset.W)_h$($preset.H).png"
            $bitmap.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
            Write-Host "$($preset.Name): x=$x y=$y w=$w h=$h -> $outPath"
        } finally {
            $bitmap.Dispose()
        }
    }
} finally {
    $image.Dispose()
}
