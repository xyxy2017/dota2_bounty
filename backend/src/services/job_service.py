from __future__ import annotations

from services.session_tracker import SessionTracker
from storage.repositories.jobs_repo import JobsRepository


class JobService:
    RESOLVE_MATCH_JOB = "resolve_match"

    def __init__(
        self,
        jobs_repo: JobsRepository,
        session_tracker: SessionTracker,
        default_local_player_id: str | None = None,
    ) -> None:
        self.jobs_repo = jobs_repo
        self.session_tracker = session_tracker
        self.default_local_player_id = default_local_player_id

    def enqueue_match_resolution(
        self,
        temp_match_key: str | None,
        match_id: str | None = None,
        local_player_id: str | None = None,
        manual_result: dict | None = None,
        max_attempts: int = 5,
        reopen_done: bool = True,
    ) -> str | None:
        key = temp_match_key or (f"match-{match_id}" if match_id else None)
        if not key:
            return None
        resolved_local_player_id = local_player_id or self.default_local_player_id
        payload = {
            "temp_match_key": temp_match_key,
            "match_id": match_id,
            "local_player_id": resolved_local_player_id,
            "manual_result": manual_result,
        }
        unique_key = f"{key}:{match_id or 'unknown'}"
        return self.jobs_repo.enqueue_unique(
            self.RESOLVE_MATCH_JOB,
            unique_key=unique_key,
            payload=payload,
            max_attempts=max_attempts,
            reopen_done=reopen_done,
        )

    def enqueue_from_gsi_payload(self, payload: dict) -> str | None:
        map_data = payload.get("map", {})
        match_id = map_data.get("matchid")
        player_data = payload.get("player", {})
        provider_data = payload.get("provider", {})
        local_player_id = (
            player_data.get("accountid")
            or player_data.get("account_id")
            or player_data.get("steamid")
            or player_data.get("steam_id")
            or provider_data.get("accountid")
            or provider_data.get("account_id")
            or provider_data.get("steamid")
            or provider_data.get("steam_id")
        )
        local_player_id = local_player_id or self.default_local_player_id
        temp_match_key = self.session_tracker.current.temp_match_key
        return self.enqueue_match_resolution(
            temp_match_key=temp_match_key,
            match_id=str(match_id) if match_id else None,
            local_player_id=str(local_player_id) if local_player_id else None,
            max_attempts=180,
            reopen_done=False,
        )
