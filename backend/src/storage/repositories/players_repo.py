from __future__ import annotations

from datetime import datetime, timezone

from storage.db import Database


class PlayersRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_seen_player(self, player_id: str, name: str | None, seen_at: str | None) -> None:
        now = seen_at or datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO players (
                    steam_id, latest_name, tag, note, first_seen_at, last_seen_at,
                    encounter_count, updated_at
                ) VALUES (?, ?, NULL, NULL, ?, ?, 1, ?)
                ON CONFLICT(steam_id) DO UPDATE SET
                    latest_name = COALESCE(excluded.latest_name, players.latest_name),
                    last_seen_at = excluded.last_seen_at,
                    encounter_count = players.encounter_count + 1,
                    updated_at = excluded.updated_at
                """,
                (player_id, name, now, now, now),
            )

    def get_by_ids(self, player_ids: list[str]) -> dict[str, dict]:
        if not player_ids:
            return {}
        placeholders = ",".join("?" for _ in player_ids)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM players WHERE steam_id IN ({placeholders})",
                player_ids,
            ).fetchall()
        return {row["steam_id"]: dict(row) for row in rows}

    def get_by_id(self, player_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM players WHERE steam_id = ?",
                (player_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_player_meta(self, player_id: str, tag: str | None, note: str | None) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE players
                SET tag = ?, note = ?, updated_at = ?
                WHERE steam_id = ?
                """,
                (tag, note, now, player_id),
            )
        return self.get_by_id(player_id)

    def list_recent_players(self, limit: int = 20, exclude_player_id: str | None = None) -> list[dict]:
        safe_limit = max(1, min(200, limit))
        with self.db.connect() as conn:
            params: list[object] = []
            where = """
                (
                    NOT EXISTS (
                        SELECT 1
                        FROM encounters real_any
                        WHERE real_any.match_id IS NOT NULL
                          AND (real_any.source IS NULL OR real_any.source != 'mock_payload')
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM encounters e
                        WHERE e.player_steam_id = players.steam_id
                          AND e.match_id IS NOT NULL
                          AND (e.source IS NULL OR e.source != 'mock_payload')
                    )
                )
            """
            if exclude_player_id:
                where = f"(steam_id != ?) AND {where}"
                params.append(exclude_player_id)
            rows = conn.execute(
                f"""
                SELECT *
                FROM players
                WHERE {where}
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                [*params, safe_limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def count_all(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM players").fetchone()
        return int(row["c"]) if row else 0
