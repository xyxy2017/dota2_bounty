from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1

from domain.enums import DataStatus
from domain.models import ResolvedMatchResult, ResolvedPlayer
from storage.db import Database
from storage.repositories.encounters_repo import EncountersRepository


def test_apply_resolved_match_updates_existing_id_conflict(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = EncountersRepository(db)
    now = datetime.now(timezone.utc).isoformat()
    stale_id = sha1("8811965527:42".encode("utf-8")).hexdigest()

    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO encounters (
                id, match_id, temp_match_key, player_steam_id, player_name,
                player_hero_id, my_hero_id, same_team, result, played_at,
                data_status, source, created_at, updated_at
            ) VALUES (?, NULL, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                stale_id,
                "old-temp",
                "42",
                "Old Name",
                "unknown",
                now,
                DataStatus.PENDING.value,
                "legacy",
                now,
                now,
            ),
        )

    updated = repo.apply_resolved_match(
        ResolvedMatchResult(
            match_id="8811965527",
            temp_match_key="current-temp",
            local_player_id="126600075",
            result="win",
            source="opendota",
            ended_at=now,
            players=[
                ResolvedPlayer(player_id="126600075", name="Me", team="radiant", hero_id=88, is_local_player=True),
                ResolvedPlayer(player_id="42", name="Real Name", team="dire", hero_id=14),
            ],
        )
    )

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT match_id, temp_match_key, player_steam_id, player_name, data_status, source
            FROM encounters
            WHERE player_steam_id = ?
            """,
            ("42",),
        ).fetchall()

    assert updated >= 2
    assert len(rows) == 1
    assert rows[0]["match_id"] == "8811965527"
    assert rows[0]["temp_match_key"] == "current-temp"
    assert rows[0]["player_name"] == "Real Name"
    assert rows[0]["data_status"] == DataStatus.COMPLETED.value
    assert rows[0]["source"] == "opendota"


def test_apply_resolved_match_is_idempotent_for_completed_rows(tmp_path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = EncountersRepository(db)
    now = datetime.now(timezone.utc).isoformat()
    resolved = ResolvedMatchResult(
        match_id="8811965527",
        temp_match_key="current-temp",
        local_player_id="126600075",
        result="win",
        source="opendota",
        ended_at=now,
        players=[
            ResolvedPlayer(player_id="126600075", name="Me", team="radiant", hero_id=88, is_local_player=True),
            ResolvedPlayer(player_id="42", name="Real Name", team="dire", hero_id=14),
        ],
    )

    repo.apply_resolved_match(resolved)
    repo.apply_resolved_match(resolved)

    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM encounters
            WHERE match_id = ? AND player_steam_id = ?
            """,
            ("8811965527", "42"),
        ).fetchone()

    assert row["count"] == 1
