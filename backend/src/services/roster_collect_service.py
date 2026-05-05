from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from domain.models import NormalizedPlayer, RosterSnapshot, SessionSnapshot


class RosterCollectService:
    def collect(self, payload: dict[str, Any], session: SessionSnapshot) -> RosterSnapshot:
        players: list[NormalizedPlayer] = []
        seen_ids: set[str] = set()

        candidate_id = self._extract_player_id(payload)
        candidate_name = self._extract_player_name(payload)
        candidate_hero_name = self._extract_hero_name(payload)

        if candidate_id:
            players.append(
                NormalizedPlayer(
                    player_id=candidate_id,
                    name=candidate_name,
                    hero_name=candidate_hero_name,
                    is_local_player=True,
                )
            )
            seen_ids.add(candidate_id)

        for player_obj in self._extract_player_candidates(payload):
            player_id = player_obj.get("steam_id") or player_obj.get("account_id")
            if not player_id:
                continue
            player_id = str(player_id)
            if player_id in seen_ids:
                continue
            players.append(
                NormalizedPlayer(
                    player_id=player_id,
                    name=player_obj.get("name"),
                    hero_name=player_obj.get("hero_name"),
                    hero_id=self._safe_int(player_obj.get("hero_id")),
                    team=player_obj.get("team"),
                )
            )
            seen_ids.add(player_id)

        return RosterSnapshot(
            temp_match_key=session.temp_match_key,
            collected_at=datetime.now(timezone.utc).isoformat(),
            players=players,
            completeness="completed" if len(players) >= 2 else "partial",
            raw_payload=payload,
        )

    def _extract_player_id(self, payload: dict[str, Any]) -> str | None:
        provider = payload.get("provider", {})
        for key in ("steamid", "steam_id", "accountid", "account_id"):
            if provider.get(key):
                return str(provider[key])
        player = payload.get("player", {})
        for key in ("steamid", "steam_id", "accountid", "account_id"):
            if player.get(key):
                return str(player[key])
        return None

    def _extract_player_name(self, payload: dict[str, Any]) -> str | None:
        provider = payload.get("provider", {})
        return provider.get("name") or payload.get("player", {}).get("name")

    def _extract_hero_name(self, payload: dict[str, Any]) -> str | None:
        hero = payload.get("hero", {})
        return hero.get("name")

    def _extract_player_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if any(k in node for k in ("steam_id", "account_id", "hero_id", "hero_name", "team")):
                    candidates.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return candidates

    def _safe_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
