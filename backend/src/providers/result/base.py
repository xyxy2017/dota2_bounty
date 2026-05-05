from __future__ import annotations

from typing import Protocol

from domain.models import MatchResolveContext, ResolvedMatchResult


class MatchResultProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...
    def resolve(self, ctx: MatchResolveContext) -> ResolvedMatchResult | None: ...
