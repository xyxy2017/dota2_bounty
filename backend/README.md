# Backend MVP

## Run

Install dependencies:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Start the server:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
PYTHONPATH=src python -m app.main
```

Initialize the database manually:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python scripts/init_db.py
```

Replay a saved payload:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python scripts/replay_payload.py /path/to/payload.json
```

Manually resolve a match from a prepared JSON payload:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python scripts/resolve_manual.py /path/to/resolved-match.json
```

Set player tag/note from CLI:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python scripts/set_player_meta.py <player_id> --tag enemy --note "mid toxic"
```

Inspect recent records from CLI:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python scripts/query_recent.py --events 20 --encounters 20 --players 20
python scripts/query_recent.py --repeats 20 --min-encounters 2 --exclude-player-id 126600075
```

Watch current alert feed and send local notifications:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python scripts/watch_alerts.py --once
python scripts/watch_alerts.py --interval 2
```

Backfill real historical matches from OpenDota:

```bash
cd /Users/jiangxinyuan/vibe_code/dota2_bounty/backend
python scripts/backfill_recent.py --account-id 126600075 --mode preview --limit 20
python scripts/backfill_recent.py --account-id 126600075 --mode resolve --limit 20
python scripts/backfill_recent.py --account-id 126600075 --mode resolve --limit 50
```

If you prefer env-based account config:

```bash
export DOTA2_BOUNTY_ACCOUNT_ID=126600075
python scripts/backfill_recent.py --mode resolve --limit 20
```

## Runtime outputs

- SQLite DB: `backend/runtime/app.db`
- Logs: `backend/runtime/logs/backend.log`
- Status file: `backend/runtime/runtime-status.json`
- Alert feed: `backend/runtime/alerts.json`
- Hero catalog cache: `backend/runtime/hero-catalog.json`

## Auto OCR Foreground Rules

- Auto OCR uses Windows `CopyFromScreen`, so capture follows desktop foreground/visibility restrictions.
- Default behavior requires Dota 2 to already be foreground; otherwise state is `dota_not_foreground` and OCR is skipped.
- Optional focus mode (`DOTA2_BOUNTY_AUTO_OCR_AUTO_FOCUS=1` or `.\scripts\start_backend.ps1 -AutoFocusDota`) attempts Restore/ShowWindow/SetForegroundWindow first.
- Focus mode can interrupt the currently active window.
- The capture helper declares DPI awareness before reading Dota 2 window coordinates, so scaled displays use physical pixels for capture regions.

## Current MVP scope

- `POST /gsi` receives raw GSI payloads
- GSI events are stored in SQLite
- Session state is tracked with a temporary match key
- Roster candidates are extracted best-effort from incoming payloads
- Historical hits are resolved from the local DB
- Runtime state is written to `runtime-status.json`
- Operator-facing APIs:
  - `GET /alerts/current`
  - `GET /summary`
  - `GET /tags/presets`
  - `GET /players/recent`
  - `GET /players/repeats?limit=20&min_encounters=2&exclude_player_id=126600075`
  - `GET /players/{player_id}/history-summary?limit=5`
  - `GET /players/{player_id}`
  - `PATCH /players/{player_id}`
  - `GET /encounters/recent`
  - `GET /encounters/player/{player_id}`
  - `GET /events/recent`
  - `POST /resolve/now`
  - `POST /resolve/enqueue`
  - `POST /resolve/jobs`
  - `GET /resolve/jobs`
  - `GET /backfill/recent?account_id=126600075&limit=20`
  - `POST /backfill/enqueue` with `{"account_id":"126600075","limit":20}`
  - `POST /backfill/resolve` with `{"account_id":"126600075","limit":20}`

## GSI hit summary

When `/gsi` detects historical players, each item in `hits` now includes:

- `encounter_count`
- `teammate_count`
- `opponent_count`
- `win_count`
- `lose_count`
- `last_match_id`
- `last_same_team`
- `last_player_hero_id`
- `last_my_hero_id`
- `last_relation`
- `priority_score`
- `summary_text`
- `last_player_hero_name`
- `last_my_hero_name`

`/gsi` also returns:

- `hit_count`
- `top_hits` (first 3 sorted hits for direct reminder usage)

Each `/gsi` ingest also rewrites `backend/runtime/alerts.json` with the current top reminders.

## Tag presets

`GET /tags/presets` returns recommended operator tags such as:

- `仇人`
- `毒瘤`
- `避雷`
- `关注`
- `大腿`
- `靠谱队友`
- `绝活哥`

Tagged players receive higher alert priority and more direct reminder wording.

## Minimal manual resolve payload

`POST /resolve/now` accepts:

```json
{
  "manual_result": {
    "match_id": "demo-001",
    "result": "win",
    "ended_at": "2026-04-29T15:30:00+00:00",
    "players": [
      {
        "player_id": "local-steam-id",
        "name": "me",
        "hero_id": 74,
        "team": "radiant",
        "is_local_player": true
      },
      {
        "player_id": "other-steam-id",
        "name": "enemy",
        "hero_id": 11,
        "team": "dire"
      }
    ]
  }
}
```

## OpenDota provider notes

- Default API base: `https://api.opendota.com/api`
- `limit <= 20` uses `recentMatches`
- `limit > 20` uses paged player match history via `/players/{account_id}/matches`
- Match resolve source order:
  - `mock_payload` (when `manual_result` is provided)
  - `opendota` (real network fetch by `match_id`)
  - `noop`
- If OpenDota has temporary delays for public data, run backfill again later.
