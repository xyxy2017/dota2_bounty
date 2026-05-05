from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS players (
                    steam_id TEXT PRIMARY KEY,
                    latest_name TEXT,
                    tag TEXT,
                    note TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    encounter_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS encounters (
                    id TEXT PRIMARY KEY,
                    match_id TEXT,
                    temp_match_key TEXT,
                    player_steam_id TEXT NOT NULL,
                    player_name TEXT,
                    player_hero_id INTEGER,
                    my_hero_id INTEGER,
                    same_team INTEGER,
                    result TEXT NOT NULL,
                    played_at TEXT,
                    data_status TEXT NOT NULL,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(match_id, player_steam_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    temp_match_key TEXT,
                    my_steam_id TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    result TEXT NOT NULL,
                    data_status TEXT NOT NULL,
                    source TEXT,
                    raw_payload_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    unique_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    next_run_at TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_type, unique_key)
                );

                CREATE INDEX IF NOT EXISTS idx_encounters_player_steam_id
                ON encounters(player_steam_id);

                CREATE INDEX IF NOT EXISTS idx_encounters_played_at
                ON encounters(played_at);

                CREATE INDEX IF NOT EXISTS idx_events_created_at
                ON events(created_at);

                CREATE INDEX IF NOT EXISTS idx_matches_temp_match_key
                ON matches(temp_match_key);

                CREATE INDEX IF NOT EXISTS idx_jobs_ready
                ON jobs(status, next_run_at);
                """
            )
