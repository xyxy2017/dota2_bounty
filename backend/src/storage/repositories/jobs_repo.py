from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from storage.db import Database


class JobsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue_unique(
        self,
        job_type: str,
        unique_key: str,
        payload: dict,
        max_attempts: int = 5,
        reopen_done: bool = True,
    ) -> str:
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        job_id = str(uuid4())
        with self.db.connect() as conn:
            if not reopen_done:
                row = conn.execute(
                    """
                    SELECT id, status
                    FROM jobs
                    WHERE job_type = ? AND unique_key = ?
                    """,
                    (job_type, unique_key),
                ).fetchone()
                if row and row["status"] == "done":
                    return str(row["id"])

            conn.execute(
                """
                INSERT INTO jobs (
                    id, job_type, unique_key, status, payload_json, attempt_count,
                    max_attempts, next_run_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL, ?, ?)
                ON CONFLICT(job_type, unique_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    status = CASE
                        WHEN jobs.status IN ('done', 'failed') THEN 'pending'
                        ELSE jobs.status
                    END,
                    next_run_at = excluded.next_run_at,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    job_type,
                    unique_key,
                    "pending",
                    json.dumps(payload, ensure_ascii=True),
                    max_attempts,
                    now_str,
                    now_str,
                    now_str,
                ),
            )
            row = conn.execute(
                """
                SELECT id
                FROM jobs
                WHERE job_type = ? AND unique_key = ?
                """,
                (job_type, unique_key),
            ).fetchone()
        return str(row["id"])

    def claim_ready(self, job_type: str, limit: int = 10) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        claimed: list[dict] = []
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE job_type = ?
                  AND status = 'pending'
                  AND next_run_at <= ?
                ORDER BY next_run_at ASC, created_at ASC
                LIMIT ?
                """,
                (job_type, now, limit),
            ).fetchall()
            for row in rows:
                update = conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'running', updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, row["id"]),
                )
                if update.rowcount == 1:
                    claimed.append(dict(row))
        return claimed

    def mark_done(self, job_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'done', updated_at = ?
                WHERE id = ?
                """,
                (now, job_id),
            )

    def mark_retry(self, job_id: str, error: str, delay_seconds: int = 30) -> None:
        now = datetime.now(timezone.utc)
        next_run = now + timedelta(seconds=delay_seconds)
        now_str = now.isoformat()
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT attempt_count, max_attempts
                FROM jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if not row:
                return
            attempts = int(row["attempt_count"]) + 1
            max_attempts = int(row["max_attempts"])
            status = "failed" if attempts >= max_attempts else "pending"
            conn.execute(
                """
                UPDATE jobs
                SET
                    status = ?,
                    attempt_count = ?,
                    next_run_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, attempts, next_run.isoformat(), error[:1000], now_str, job_id),
            )

    def list_recent(self, limit: int = 50) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
