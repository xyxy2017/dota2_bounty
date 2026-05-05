from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
from typing import Any

from domain.enums import SessionState
from domain.models import SessionSnapshot


class SessionTracker:
    def __init__(self) -> None:
        self.current: SessionSnapshot = SessionSnapshot(
            temp_match_key=None,
            state=SessionState.IDLE,
            started_at=None,
            last_seen_at=None,
        )

    def update(self, payload: dict[str, Any]) -> SessionSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        map_data = payload.get("map", {})
        game_state = map_data.get("game_state")
        match_id = map_data.get("matchid")
        map_name = map_data.get("name")

        active = self._is_match_active(payload)
        if active and not self.current.temp_match_key:
            self.current = SessionSnapshot(
                temp_match_key=self._build_temp_key(match_id, map_name, now),
                state=SessionState.IN_MATCH,
                started_at=now,
                last_seen_at=now,
                map_name=map_name,
                game_state=game_state,
            )
            return self.current

        if active:
            self.current.last_seen_at = now
            self.current.game_state = game_state
            self.current.map_name = map_name
            self.current.state = SessionState.IN_MATCH
            return self.current

        if self.current.temp_match_key:
            self.current.last_seen_at = now
            self.current.state = SessionState.POST_MATCH_PENDING
            return self.current

        return self.current

    def _build_temp_key(self, match_id: Any, map_name: Any, now: str) -> str:
        raw = f"{match_id}:{map_name}:{now}"
        return sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _is_match_active(self, payload: dict[str, Any]) -> bool:
        map_data = payload.get("map", {})
        if not isinstance(map_data, dict) or not map_data:
            return False

        meaningful_keys = ("game_state", "matchid", "name", "clock_time", "daytime")
        if any(map_data.get(key) not in (None, "") for key in meaningful_keys):
            return True

        return bool(payload.get("hero")) or bool(payload.get("player"))
