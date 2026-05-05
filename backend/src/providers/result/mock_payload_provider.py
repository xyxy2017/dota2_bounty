from __future__ import annotations

from typing import Any

from domain.models import MatchResolveContext, ResolvedMatchResult, ResolvedPlayer


class MockPayloadResultProvider:
    name = "mock_payload"

    def is_available(self) -> bool:
        return True

    def resolve(self, ctx: MatchResolveContext) -> ResolvedMatchResult | None:
        payload = ctx.manual_result
        if not payload:
            return None

        match_id = payload.get("match_id") or ctx.match_id
        if not match_id:
            return None

        players_raw = payload.get("players", [])
        players: list[ResolvedPlayer] = []
        for p in players_raw:
            player_id = p.get("player_id")
            if not player_id:
                continue
            players.append(
                ResolvedPlayer(
                    player_id=str(player_id),
                    name=p.get("name"),
                    hero_id=_safe_int(p.get("hero_id")),
                    team=p.get("team"),
                    is_local_player=bool(p.get("is_local_player")),
                )
            )

        return ResolvedMatchResult(
            match_id=str(match_id),
            temp_match_key=ctx.temp_match_key,
            local_player_id=ctx.local_player_id,
            result=payload.get("result", "unknown"),
            players=players,
            started_at=payload.get("started_at"),
            ended_at=payload.get("ended_at"),
            source=self.name,
            completeness=payload.get("completeness", "completed"),
            raw_payload=payload,
        )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
