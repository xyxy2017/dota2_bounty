from __future__ import annotations

import json
from datetime import datetime, timezone

from domain.enums import DataStatus
from domain.models import ResolvedMatchResult
from storage.db import Database


class MatchesRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_resolved_match(self, resolved: ResolvedMatchResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO matches (
                    match_id, temp_match_key, my_steam_id, started_at, ended_at,
                    result, data_status, source, raw_payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    temp_match_key = COALESCE(excluded.temp_match_key, matches.temp_match_key),
                    my_steam_id = COALESCE(excluded.my_steam_id, matches.my_steam_id),
                    started_at = COALESCE(excluded.started_at, matches.started_at),
                    ended_at = COALESCE(excluded.ended_at, matches.ended_at),
                    result = excluded.result,
                    data_status = excluded.data_status,
                    source = excluded.source,
                    raw_payload_json = COALESCE(excluded.raw_payload_json, matches.raw_payload_json),
                    updated_at = excluded.updated_at
                """,
                (
                    resolved.match_id,
                    resolved.temp_match_key,
                    resolved.local_player_id,
                    resolved.started_at,
                    resolved.ended_at,
                    resolved.result,
                    DataStatus.COMPLETED.value,
                    resolved.source,
                    json.dumps(resolved.raw_payload or {}, ensure_ascii=True),
                    now,
                    now,
                ),
            )
