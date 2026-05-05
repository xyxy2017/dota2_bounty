from __future__ import annotations

from domain.models import RosterSnapshot


class GsiRosterProvider:
    def __init__(self) -> None:
        self._latest: RosterSnapshot | None = None

    def is_available(self) -> bool:
        return self._latest is not None

    def get_roster(self) -> RosterSnapshot | None:
        return self._latest

    def set_latest(self, roster: RosterSnapshot) -> None:
        self._latest = roster
