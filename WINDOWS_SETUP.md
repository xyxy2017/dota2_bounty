# Dota2 Bounty Windows Setup

## 1. Environment

Install the following first:

- Windows 10/11
- Python 3.11+
- Git
- Steam + Dota 2

Optional but recommended:

- PowerShell 7
- VS Code

## 2. Clone Repository

Use SSH:

```powershell
git clone git@github.com:xyxy2017/dota2_bounty.git
cd dota2_bounty\backend
```

## 3. Python Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## 4. Start Backend

Recommended one-command restart:

```powershell
.\backend\scripts\start_backend.ps1 -OpenDashboard -OpenOcrLive
```

The script stops any old process on port 8000, removes a stale backend lock,
starts the backend, checks `/health`, and optionally opens the dashboard/OCR page.

Manual start:

```powershell
cd backend
$env:PYTHONPATH="src"
python -m app.main
```

Health check in another PowerShell window:

```powershell
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"ok": true}
```

## 5. Open Local Dashboard

Open:

- `http://127.0.0.1:8000/`

You should see:

- 当前提醒
- 服务概览
- 高频玩家
- 最近玩家
- 玩家详情

## 6. Configure Dota 2 GSI

Copy this file:

- `backend\runtime\gamestate_integration_dota2_bounty.cfg.example`

To Dota cfg folder and rename it to:

- `gamestate_integration_dota2_bounty.cfg`

Typical Steam path:

```text
C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta\game\dota\cfg\
```

Then restart Dota 2.

## 7. Backfill Real Match History

Use your account id:

```powershell
cd backend
python scripts\backfill_recent.py --account-id 126600075 --mode resolve --limit 50
```

Optional env default:

```powershell
$env:DOTA2_BOUNTY_ACCOUNT_ID="126600075"
python scripts\backfill_recent.py --mode resolve --limit 20
```

## 8. Validate In-Game Hit

1. Start backend.
2. Open dashboard.
3. Enter a real match or bot match.
4. Check if 当前提醒 updates.
5. After match, run backfill again.
6. Enter next match and validate repeat-player hits.

## 8.1 Live OCR Draft Test

Open:

- `http://127.0.0.1:8000/debug/ocr/live`

Recommended settings:

- `选人顶部昵称栏` first; if names are below hero portraits, use `选人底部昵称栏`.
- `interval`: `6s`
- `threshold`: `0.72` to start, raise to `0.82` if false positives appear.
- `min encounters`: `2` or higher.
- `tagged only`: enable it for ranked/live use if you only want important marked players.
- `confirm scans`: `2` for safer live alerts, `1` for faster but noisier alerts.

Notes:

- OCR is nickname-based and cannot prove the live account id.
- Post-match backfill remains the source of truth for real account ids.
- If a hit appears, the page stops automatically and publishes to `/alerts/current`.

## 8.2 Backend Auto OCR

Backend Auto OCR can capture the Dota 2 window without browser screen-share permission.
It uses the same tight top-left/top-right draft-name regions:

- left5: `x=10 y=8 w=33 h=3`
- right5: `x=57 y=8 w=33 h=3`

This feature is optional. Without an OCR runtime, it can still capture debug images,
but it cannot convert the images into text.

Important capture behavior:

- The backend locates the real `dota2.exe` process first.
- `CopyFromScreen` captures desktop pixels, so Windows foreground/visibility rules apply.
- Screen capture is only trustworthy when Dota 2 is the foreground, visible window.
- By default, if Dota 2 is not foreground, Auto OCR returns `dota_not_foreground` and skips OCR.
- Optional focus mode can bring Dota 2 to the foreground before capture, but it may interrupt the current active window.
- Focus mode (`-AutoFocusDota` or `DOTA2_BOUNTY_AUTO_OCR_AUTO_FOCUS=1`) may steal focus from your current app.
- The capture helper is DPI-aware before reading the window rectangle, so Windows display scaling such as 125% on a 2560x1440 monitor is handled with physical pixels instead of virtual 2048x1152 coordinates.

For development, install Tesseract for Windows, then start with:

```powershell
.\backend\scripts\start_backend.ps1 -AutoOcr -OpenDashboard -OpenOcrLive -TesseractPath "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

To allow the backend to focus Dota 2 before capture:

```powershell
.\backend\scripts\start_backend.ps1 -AutoOcr -AutoFocusDota -OpenDashboard -OpenOcrLive
```

For release builds, bundle Tesseract inside the app so users do not need to install or configure it separately:

```text
backend/tools/tesseract/tesseract.exe
backend/tools/tesseract/libtesseract-5.dll
backend/tools/tesseract/libleptonica-6.dll
backend/tools/tesseract/tessdata/eng.traineddata
backend/tools/tesseract/tessdata/chi_sim.traineddata
```

Before publishing a build, run:

```powershell
.\backend\scripts\check_bundled_tesseract.ps1
```

The check requires `tesseract.exe`, core OCR DLLs, and `eng.traineddata`. `chi_sim.traineddata` is optional for English-only OCR, but useful if Dota names or UI text include Chinese.

Backend Auto OCR resolves the OCR engine in this order:

1. bundled `backend/tools/tesseract/tesseract.exe`
2. bundled top-level `tools/tesseract/tesseract.exe`
3. `DOTA2_BOUNTY_TESSERACT_PATH` or the `-TesseractPath` script parameter
4. `tesseract.exe` on PATH
5. common Windows install locations

- `C:\Program Files\Tesseract-OCR\tesseract.exe`
- `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`

Backend Auto OCR now saves both raw captures and OCR-preprocessed captures:

- `backend/runtime/auto-ocr/left5.png`
- `backend/runtime/auto-ocr/right5.png`
- `backend/runtime/auto-ocr/left5.ocr.png`
- `backend/runtime/auto-ocr/right5.ocr.png`
- `backend/runtime/auto-ocr/slot-radiant-1.png` ... `slot-radiant-5.png`
- `backend/runtime/auto-ocr/slot-dire-1.png` ... `slot-dire-5.png`

The backend also writes ten per-player slot captures. Matching prefers the
per-slot OCR text first, and only falls back to the left5/right5 group text if
the slot text is empty.

Useful tuning knobs:

```powershell
$env:DOTA2_BOUNTY_TESSERACT_LANG="eng"
$env:DOTA2_BOUNTY_TESSERACT_PSM="7"
$env:DOTA2_BOUNTY_TESSERACT_OEM="1"
$env:DOTA2_BOUNTY_AUTO_OCR_IMAGE_SCALE="4"
$env:DOTA2_BOUNTY_AUTO_OCR_IMAGE_THRESHOLD="175"
$env:DOTA2_BOUNTY_AUTO_OCR_CONFIRM_SCANS="2"
$env:DOTA2_BOUNTY_AUTO_OCR_COOLDOWN_SECONDS="180"
$env:DOTA2_BOUNTY_AUTO_OCR_DEBUG_IMAGES="1"
$env:DOTA2_BOUNTY_AUTO_OCR_DEBUG_MAX_RUNS="80"
```

The same knobs can be passed through the start script:

```powershell
.\backend\scripts\start_backend.ps1 -AutoOcr -OpenDashboard -OpenOcrLive -TesseractLang eng -TesseractPsm 7 -AutoOcrImageScale 4 -AutoOcrImageThreshold 175 -AutoOcrConfirmScans 2 -AutoOcrCooldownSeconds 180
```

`confirm scans` means the same matched player set must appear in consecutive
backend OCR scans before the alert is published. `cooldown seconds` prevents the
same matched player set from being published repeatedly while the draft screen
is unchanged.

To keep every backend OCR capture for debugging:

```powershell
.\backend\scripts\start_backend.ps1 -AutoOcr -AutoFocusDota -AutoOcrDebugImages -OpenDashboard -OpenOcrLive
```

Debug mode writes one folder per OCR run:

```text
backend\runtime\auto-ocr\debug\YYYYMMDD-HHMMSS-ffffff\
```

Each run folder contains `full-window.png`, `full-window.annotated.png`, and all
region crops such as `left5.png`, `slot-radiant-1.png`, plus their `.ocr.png`
preprocessed versions. Keep this off for normal use because full-window images
can grow quickly.

## 8.3 Build a Downloadable Windows Package

The release build is designed so a user can unzip one folder and run the local
backend without installing Python or Tesseract separately.

Build the one-folder Windows package:

```powershell
.\backend\scripts\build_windows_release.ps1 -IncludeTesseract
```

The output folder is:

```text
backend\dist\Dota2BountyBackend\
```

It contains:

- `Dota2BountyBackend.exe`
- `start_dota2_bounty.ps1`
- `src\web\...` local dashboard/OCR pages
- `runtime\gamestate_integration_dota2_bounty.cfg.example`
- `tools\tesseract\...` when built with `-IncludeTesseract`

Run the packaged app:

```powershell
.\backend\dist\Dota2BountyBackend\start_dota2_bounty.ps1 -AutoOcr -AutoFocusDota -OpenDashboard -OpenOcrLive
```

The packaged app writes its own runtime files inside the release folder, so the
downloaded app stays self-contained.

Useful endpoints:

- `GET http://127.0.0.1:8000/debug/ocr/auto`
- `POST http://127.0.0.1:8000/debug/ocr/auto/run-once`
- `POST http://127.0.0.1:8000/debug/ocr/auto/start`
- `POST http://127.0.0.1:8000/debug/ocr/auto/stop`

Runtime captures are written to:

- `backend\runtime\auto-ocr\left5.png`
- `backend\runtime\auto-ocr\right5.png`

Performance notes:

- Default interval is `6s`, so it is not continuously recording.
- Each loop captures two small strips, not the whole screen.
- OCR only runs when Tesseract is configured.
- If you see FPS impact, raise `DOTA2_BOUNTY_AUTO_OCR_INTERVAL_SECONDS` to `10` or `15`.

## 9. Logs and Runtime Files

Main paths:

- DB: `backend\runtime\app.db`
- backend log: `backend\runtime\logs\backend.log`
- status: `backend\runtime\runtime-status.json`
- alerts feed: `backend\runtime\alerts.json`

## 10. Known Notes

- If `backend.lock` remains after forced stop, delete:
  - `backend\runtime\backend.lock`
- If port 8000 is occupied, run with another port:

```powershell
$env:DOTA2_BOUNTY_SERVER_PORT="8123"
$env:PYTHONPATH="src"
python -m app.main
```

Then open `http://127.0.0.1:8123/`.
