from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1

from domain.enums import DataStatus
from domain.models import EncounterUpsert, RepeatedPlayerSummary, ResolvedMatchResult
from storage.db import Database


class EncountersRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_pending_encounter(self, encounter: EncounterUpsert) -> None:
        now = datetime.now(timezone.utc).isoformat()
        encounter_id = sha1(
            f"{encounter.temp_match_key}:{encounter.player_id}".encode("utf-8")
        ).hexdigest()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO encounters (
                    id, match_id, temp_match_key, player_steam_id, player_name,
                    player_hero_id, my_hero_id, same_team, result, played_at,
                    data_status, source, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    player_name = excluded.player_name,
                    played_at = excluded.played_at,
                    updated_at = excluded.updated_at
                """,
                (
                    encounter_id,
                    encounter.temp_match_key,
                    encounter.player_id,
                    encounter.player_name,
                    "unknown",
                    encounter.played_at,
                    encounter.data_status.value,
                    encounter.source,
                    now,
                    now,
                ),
            )

    def list_recent_by_player_ids(self, player_ids: list[str]) -> dict[str, dict]:
        if not player_ids:
            return {}
        placeholders = ",".join("?" for _ in player_ids)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*
                FROM encounters e
                JOIN (
                    SELECT player_steam_id, MAX(COALESCE(played_at, created_at)) AS last_seen
                    FROM encounters
                    WHERE player_steam_id IN ({placeholders})
                    GROUP BY player_steam_id
                ) last_rows
                ON e.player_steam_id = last_rows.player_steam_id
                AND COALESCE(e.played_at, e.created_at) = last_rows.last_seen
                """,
                player_ids,
            ).fetchall()
        return {row["player_steam_id"]: dict(row) for row in rows}

    def list_history_snapshots_by_player_ids(self, player_ids: list[str]) -> dict[str, dict]:
        if not player_ids:
            return {}
        placeholders = ",".join("?" for _ in player_ids)
        with self.db.connect() as conn:
            latest_rows = conn.execute(
                f"""
                SELECT e.*
                FROM encounters e
                JOIN (
                    SELECT player_steam_id, MAX(COALESCE(played_at, created_at)) AS last_seen
                    FROM encounters
                    WHERE player_steam_id IN ({placeholders})
                      AND match_id IS NOT NULL
                      AND data_status = ?
                      AND (
                        source IS NULL
                        OR source != 'mock_payload'
                        OR NOT EXISTS (
                            SELECT 1
                            FROM encounters real_any
                            WHERE real_any.match_id IS NOT NULL
                              AND (real_any.source IS NULL OR real_any.source != 'mock_payload')
                        )
                      )
                    GROUP BY player_steam_id
                ) last_rows
                ON e.player_steam_id = last_rows.player_steam_id
                AND COALESCE(e.played_at, e.created_at) = last_rows.last_seen
                """,
                [*player_ids, DataStatus.COMPLETED.value],
            ).fetchall()
            aggregate_rows = conn.execute(
                f"""
                SELECT
                    player_steam_id,
                    COUNT(*) AS encounter_count,
                    SUM(CASE WHEN same_team = 1 THEN 1 ELSE 0 END) AS teammate_count,
                    SUM(CASE WHEN same_team = 0 THEN 1 ELSE 0 END) AS opponent_count,
                    SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS win_count,
                    SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) AS lose_count
                FROM encounters
                WHERE player_steam_id IN ({placeholders})
                  AND match_id IS NOT NULL
                  AND data_status = ?
                  AND (
                    source IS NULL
                    OR source != 'mock_payload'
                    OR NOT EXISTS (
                        SELECT 1
                        FROM encounters real_any
                        WHERE real_any.match_id IS NOT NULL
                          AND (real_any.source IS NULL OR real_any.source != 'mock_payload')
                    )
                  )
                GROUP BY player_steam_id
                """,
                [*player_ids, DataStatus.COMPLETED.value],
            ).fetchall()
        latest_map = {row["player_steam_id"]: dict(row) for row in latest_rows}
        aggregate_map = {row["player_steam_id"]: dict(row) for row in aggregate_rows}
        snapshot_map: dict[str, dict] = {}
        for player_id in player_ids:
            aggregate = aggregate_map.get(player_id)
            latest = latest_map.get(player_id)
            if not aggregate and not latest:
                continue
            snapshot_map[player_id] = {
                "encounter_count": int((aggregate or {}).get("encounter_count") or 0),
                "teammate_count": int((aggregate or {}).get("teammate_count") or 0),
                "opponent_count": int((aggregate or {}).get("opponent_count") or 0),
                "win_count": int((aggregate or {}).get("win_count") or 0),
                "lose_count": int((aggregate or {}).get("lose_count") or 0),
                "last_encounter_at": (latest or {}).get("played_at"),
                "last_result": (latest or {}).get("result"),
                "last_match_id": (latest or {}).get("match_id"),
                "last_same_team": (latest or {}).get("same_team"),
                "last_player_hero_id": (latest or {}).get("player_hero_id"),
                "last_my_hero_id": (latest or {}).get("my_hero_id"),
            }
        return snapshot_map

    def apply_resolved_match(self, resolved: ResolvedMatchResult) -> int:
        if not resolved.match_id:
            return 0
        local_player = None
        for p in resolved.players:
            if p.is_local_player:
                local_player = p
                break
        local_team = local_player.team if local_player else None
        local_hero_id = local_player.hero_id if local_player else None
        played_at = resolved.ended_at or datetime.now(timezone.utc).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        updated = 0
        with self.db.connect() as conn:
            for p in resolved.players:
                same_team = None
                if local_team and p.team:
                    same_team = 1 if local_team == p.team else 0
                matched = 0
                if resolved.temp_match_key:
                    pending_cursor = conn.execute(
                        """
                        UPDATE encounters
                        SET
                            match_id = ?,
                            player_name = COALESCE(?, player_name),
                            player_hero_id = ?,
                            my_hero_id = ?,
                            same_team = ?,
                            result = ?,
                            played_at = ?,
                            data_status = ?,
                            source = ?,
                            updated_at = ?
                        WHERE temp_match_key = ? AND player_steam_id = ?
                          AND NOT EXISTS (
                              SELECT 1
                              FROM encounters existing
                              WHERE existing.match_id = ?
                                AND existing.player_steam_id = ?
                                AND existing.id != encounters.id
                          )
                        """,
                        (
                            resolved.match_id,
                            p.name,
                            p.hero_id,
                            local_hero_id,
                            same_team,
                            resolved.result,
                            played_at,
                            DataStatus.COMPLETED.value,
                            resolved.source,
                            now,
                            resolved.temp_match_key,
                            p.player_id,
                            resolved.match_id,
                            p.player_id,
                        ),
                    )
                    matched = pending_cursor.rowcount

                if matched == 0:
                    encounter_id = sha1(f"{resolved.match_id}:{p.player_id}".encode("utf-8")).hexdigest()
                    existing = conn.execute(
                        """
                        SELECT id
                        FROM encounters
                        WHERE match_id = ? AND player_steam_id = ?
                        """,
                        (resolved.match_id, p.player_id),
                    ).fetchone()
                    if existing is None:
                        existing = conn.execute(
                            "SELECT id FROM encounters WHERE id = ?",
                            (encounter_id,),
                        ).fetchone()

                    if existing is not None:
                        upsert_cursor = conn.execute(
                            """
                            UPDATE encounters
                            SET
                                match_id = ?,
                                temp_match_key = COALESCE(?, temp_match_key),
                                player_steam_id = ?,
                                player_name = COALESCE(?, player_name),
                                player_hero_id = ?,
                                my_hero_id = ?,
                                same_team = ?,
                                result = ?,
                                played_at = ?,
                                data_status = ?,
                                source = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                resolved.match_id,
                                resolved.temp_match_key,
                                p.player_id,
                                p.name,
                                p.hero_id,
                                local_hero_id,
                                same_team,
                                resolved.result,
                                played_at,
                                DataStatus.COMPLETED.value,
                                resolved.source,
                                now,
                                existing["id"],
                            ),
                        )
                    else:
                        upsert_cursor = conn.execute(
                            """
                            INSERT INTO encounters (
                                id, match_id, temp_match_key, player_steam_id, player_name,
                                player_hero_id, my_hero_id, same_team, result, played_at,
                                data_status, source, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                encounter_id,
                                resolved.match_id,
                                resolved.temp_match_key,
                                p.player_id,
                                p.name,
                                p.hero_id,
                                local_hero_id,
                                same_team,
                                resolved.result,
                                played_at,
                                DataStatus.COMPLETED.value,
                                resolved.source,
                                now,
                                now,
                            ),
                        )
                    updated += upsert_cursor.rowcount
                else:
                    updated += matched
            if resolved.temp_match_key:
                conn.execute(
                    """
                    DELETE FROM encounters
                    WHERE temp_match_key = ?
                      AND match_id IS NULL
                      AND data_status = ?
                    """,
                    (resolved.temp_match_key, DataStatus.PENDING.value),
                )
        return updated

    def list_pending_temp_match_keys(self, limit: int = 50) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT temp_match_key
                FROM encounters
                WHERE data_status = ?
                  AND temp_match_key IS NOT NULL
                GROUP BY temp_match_key
                ORDER BY MAX(COALESCE(played_at, created_at)) DESC
                LIMIT ?
                """,
                (DataStatus.PENDING.value, limit),
            ).fetchall()
        return [str(row["temp_match_key"]) for row in rows]

    def list_recent_encounters(self, limit: int = 50) -> list[dict]:
        safe_limit = max(1, min(500, limit))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM encounters
                WHERE (
                    source IS NULL
                    OR source != 'mock_payload'
                    OR NOT EXISTS (
                        SELECT 1
                        FROM encounters real_any
                        WHERE real_any.match_id IS NOT NULL
                          AND (real_any.source IS NULL OR real_any.source != 'mock_payload')
                    )
                )
                ORDER BY COALESCE(played_at, created_at) DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_encounters_for_player(self, player_id: str, limit: int = 50) -> list[dict]:
        safe_limit = max(1, min(500, limit))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM encounters
                WHERE player_steam_id = ?
                  AND (
                    source IS NULL
                    OR source != 'mock_payload'
                    OR NOT EXISTS (
                        SELECT 1
                        FROM encounters real_any
                        WHERE real_any.match_id IS NOT NULL
                          AND (real_any.source IS NULL OR real_any.source != 'mock_payload')
                    )
                  )
                ORDER BY COALESCE(played_at, created_at) DESC
                LIMIT ?
                """,
                (player_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_completed_encounters_for_player(self, player_id: str, limit: int = 50) -> list[dict]:
        safe_limit = max(1, min(500, limit))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM encounters
                WHERE player_steam_id = ?
                  AND match_id IS NOT NULL
                  AND data_status = ?
                  AND (
                    source IS NULL
                    OR source != 'mock_payload'
                    OR NOT EXISTS (
                        SELECT 1
                        FROM encounters real_any
                        WHERE real_any.match_id IS NOT NULL
                          AND (real_any.source IS NULL OR real_any.source != 'mock_payload')
                    )
                  )
                ORDER BY COALESCE(played_at, created_at) DESC
                LIMIT ?
                """,
                (player_id, DataStatus.COMPLETED.value, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_all(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM encounters").fetchone()
        return int(row["c"]) if row else 0

    def list_repeated_players(
        self,
        *,
        limit: int = 20,
        min_encounters: int = 2,
        exclude_player_id: str | None = None,
    ) -> list[RepeatedPlayerSummary]:
        safe_limit = max(1, min(500, limit))
        safe_min = max(2, min(100, min_encounters))
        filters: list[str] = ["match_id IS NOT NULL", "data_status = ?"]
        params: list[object] = [DataStatus.COMPLETED.value]
        filters.append(
            """
            (
                source IS NULL
                OR source != 'mock_payload'
                OR NOT EXISTS (
                    SELECT 1
                    FROM encounters real_any
                    WHERE real_any.match_id IS NOT NULL
                      AND (real_any.source IS NULL OR real_any.source != 'mock_payload')
                )
            )
            """
        )
        if exclude_player_id:
            filters.append("player_steam_id != ?")
            params.append(exclude_player_id)
        where_clause = " AND ".join(filters)

        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    player_steam_id,
                    MAX(COALESCE(player_name, '')) AS latest_name,
                    COUNT(*) AS encounter_count,
                    SUM(CASE WHEN same_team = 1 THEN 1 ELSE 0 END) AS teammate_count,
                    SUM(CASE WHEN same_team = 0 THEN 1 ELSE 0 END) AS opponent_count,
                    SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS win_count,
                    SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) AS lose_count,
                    MAX(COALESCE(played_at, created_at)) AS last_encounter_at
                FROM encounters
                WHERE {where_clause}
                GROUP BY player_steam_id
                HAVING COUNT(*) >= ?
                ORDER BY encounter_count DESC, last_encounter_at DESC
                LIMIT ?
                """,
                [*params, safe_min, safe_limit],
            ).fetchall()

            result: list[RepeatedPlayerSummary] = []
            for row in rows:
                match_rows = conn.execute(
                    """
                    SELECT match_id
                    FROM encounters
                    WHERE player_steam_id = ?
                      AND match_id IS NOT NULL
                    ORDER BY COALESCE(played_at, created_at) DESC
                    LIMIT 5
                    """,
                    (row["player_steam_id"],),
                ).fetchall()
                result.append(
                    RepeatedPlayerSummary(
                        player_id=str(row["player_steam_id"]),
                        latest_name=row["latest_name"] or None,
                        encounter_count=int(row["encounter_count"] or 0),
                        teammate_count=int(row["teammate_count"] or 0),
                        opponent_count=int(row["opponent_count"] or 0),
                        win_count=int(row["win_count"] or 0),
                        lose_count=int(row["lose_count"] or 0),
                        last_encounter_at=row["last_encounter_at"],
                        match_ids=[str(match_row["match_id"]) for match_row in match_rows if match_row["match_id"]],
                    )
                )
        return result
