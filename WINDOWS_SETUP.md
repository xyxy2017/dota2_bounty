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
