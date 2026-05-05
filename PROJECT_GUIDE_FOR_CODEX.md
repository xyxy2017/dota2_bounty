# Project Guide For Codex

## Project Goal

This project is a local Dota 2 companion backend that:

- tracks encountered players
- backfills real match data from OpenDota
- detects repeated players in later matches
- produces local alert feed for reminder UI

The current UX is a single-page local dashboard served by backend.

## Main Tech Stack

- Python backend
- Built-in HTTP server (`http.server`)
- SQLite local database
- OpenDota API for historical backfill
- Dota 2 GSI payload ingest
- Static web files (`index.html`, `app.js`, `styles.css`)

## Important Folders

- `backend/src/app` bootstrapping and supervisor routing
- `backend/src/services` core business logic
- `backend/src/storage/repositories` SQLite query layer
- `backend/src/web` local dashboard static page
- `backend/scripts` manual operations and testing scripts
- `backend/runtime` runtime outputs (db/log/status/alerts)

## Critical Files

- `backend/src/app/supervisor.py`
- `backend/src/infra/http_server.py`
- `backend/src/services/gsi_ingest_service.py`
- `backend/src/services/backfill_service.py`
- `backend/src/services/history_match_service.py`
- `backend/src/services/query_service.py`
- `backend/src/services/hero_catalog_service.py`
- `backend/src/storage/repositories/encounters_repo.py`
- `backend/src/storage/repositories/players_repo.py`
- `backend/src/web/app.js`
- `backend/src/web/styles.css`

## Run Commands

Setup:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Start backend:

```bash
cd backend
PYTHONPATH=src python3 -m app.main
```

Health check:

```bash
python3 -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())"
```

## Key API Endpoints

- `GET /health`
- `GET /summary`
- `GET /alerts/current`
- `GET /tags/presets`
- `GET /players/recent`
- `GET /players/repeats`
- `GET /players/{player_id}/history-summary`
- `PATCH /players/{player_id}`
- `POST /gsi`
- `POST /backfill/resolve`

## Data Sources Priority

When real data exists, hide mock view data in list queries:

- prefer non-`mock_payload` encounters
- exclude self player with `exclude_player_id`

## Scripts For Operations

- initialize db:
  - `python scripts/init_db.py`
- backfill real matches:
  - `python scripts/backfill_recent.py --account-id 126600075 --mode resolve --limit 50`
- set player meta:
  - `python scripts/set_player_meta.py <player_id> --tag 仇人 --note "note"`
- query recent:
  - `python scripts/query_recent.py --events 20 --encounters 20 --players 20`

## Current Product Rules

- hero display should be `中文 (English)` where available
- dashboard is single-page and should avoid full-page scroll on desktop
- repeated/recent panels should scroll internally
- self player should not appear in repeated/recent lists

## Codex Execution Guidance

When implementing changes:

1. Keep routes in `supervisor.py` thin.
2. Put query/filter rules in repository layer.
3. Put formatting and ranking logic in service layer.
4. Keep static page dependency-free (no build tool required).
5. Verify with:
   - `python3 -m compileall backend/src backend/scripts`
   - direct endpoint checks against `http://127.0.0.1:8000/`

## Non-Goals For Now

- No heavy frontend framework.
- No cloud database.
- No account system.
- No anti-cheat risky game memory integration.
