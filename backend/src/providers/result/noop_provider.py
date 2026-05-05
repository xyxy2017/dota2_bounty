from __future__ import annotations

from domain.models import MatchResolveContext, ResolvedMatchResult


class NoopResultProvider:
    name = "noop"

    def is_available(self) -> bool:
        return True

    def resolve(self, ctx: MatchResolveContext) -> ResolvedMatchResult | None:
        return None
