from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from storage.db import Database


class EventsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append_event(self, event_type: str, payload: dict) -> str:
        event_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO events (id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, event_type, json.dumps(payload, ensure_ascii=True), now),
            )
        return event_id

    def list_recent_events(self, limit: int = 100, event_type: str | None = None) -> list[dict]:
        safe_limit = max(1, min(1000, limit))
        with self.db.connect() as conn:
            if event_type:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM events
                    WHERE event_type = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (event_type, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM events
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def count_all(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()
        return int(row["c"]) if row else 0
