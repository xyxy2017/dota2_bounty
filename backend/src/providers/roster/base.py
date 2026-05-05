from __future__ import annotations

from typing import Protocol

from domain.models import RosterSnapshot


class RosterProvider(Protocol):
    def is_available(self) -> bool: ...
    def get_roster(self) -> RosterSnapshot | None: ...
