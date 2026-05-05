from __future__ import annotations

from services.session_tracker import SessionTracker
from storage.repositories.jobs_repo import JobsRepository


class JobService:
    RESOLVE_MATCH_JOB = "resolve_match"

    def __init__(self, jobs_repo: JobsRepository, session_tracker: SessionTracker) -> None:
        self.jobs_repo = jobs_repo
        self.session_tracker = session_tracker

    def enqueue_match_resolution(
        self,
        temp_match_key: str | None,
        match_id: str | None = None,
        local_player_id: str | None = None,
        manual_result: dict | None = None,
    ) -> str | None:
        key = temp_match_key or (f"match-{match_id}" if match_id else None)
        if not key:
            return None
        payload = {
            "temp_match_key": temp_match_key,
            "match_id": match_id,
            "local_player_id": local_player_id,
            "manual_result": manual_result,
        }
        unique_key = f"{key}:{match_id or 'unknown'}"
        return self.jobs_repo.enqueue_unique(
            self.RESOLVE_MATCH_JOB,
            unique_key=unique_key,
            payload=payload,
        )

    def enqueue_from_gsi_payload(self, payload: dict) -> str | None:
        map_data = payload.get("map", {})
        match_id = map_data.get("matchid")
        local_player_id = payload.get("provider", {}).get("steamid")
        temp_match_key = self.session_tracker.current.temp_match_key
        return self.enqueue_match_resolution(
            temp_match_key=temp_match_key,
            match_id=str(match_id) if match_id else None,
            local_player_id=str(local_player_id) if local_player_id else None,
        )
