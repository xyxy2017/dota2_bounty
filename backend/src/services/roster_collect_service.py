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
            player_id = self._normalize_player_id(
                account_id=player_obj.get("account_id") or player_obj.get("accountid"),
                steam_id=player_obj.get("steam_id") or player_obj.get("steamid"),
            )
            if not player_id:
                continue
            if player_id in seen_ids:
                continue
            hero_obj = player_obj.get("hero")
            hero_id = player_obj.get("hero_id")
            hero_name = player_obj.get("hero_name")
            if isinstance(hero_obj, dict):
                hero_id = hero_id if hero_id is not None else hero_obj.get("id")
                hero_name = hero_name or hero_obj.get("name")
            elif isinstance(hero_obj, str):
                hero_name = hero_name or hero_obj
            players.append(
                NormalizedPlayer(
                    player_id=player_id,
                    name=player_obj.get("name"),
                    hero_name=hero_name,
                    hero_id=self._safe_int(hero_id),
                    team=player_obj.get("team") or player_obj.get("team_name"),
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
        provider_id = self._normalize_player_id(
            account_id=provider.get("accountid") or provider.get("account_id"),
            steam_id=provider.get("steamid") or provider.get("steam_id"),
        )
        if provider_id:
            return provider_id
        player = payload.get("player", {})
        return self._normalize_player_id(
            account_id=player.get("accountid") or player.get("account_id"),
            steam_id=player.get("steamid") or player.get("steam_id"),
        )

    def _extract_player_name(self, payload: dict[str, Any]) -> str | None:
        provider = payload.get("provider", {})
        return payload.get("player", {}).get("name") or provider.get("name")

    def _extract_hero_name(self, payload: dict[str, Any]) -> str | None:
        hero = payload.get("hero", {})
        return hero.get("name")

    def _extract_player_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if any(
                    k in node
                    for k in (
                        "steam_id",
                        "account_id",
                        "steamid",
                        "accountid",
                        "hero_id",
                        "hero_name",
                        "hero",
                        "team",
                        "team_name",
                    )
                ):
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

    def _normalize_player_id(self, *, account_id: Any = None, steam_id: Any = None) -> str | None:
        if account_id not in (None, ""):
            return str(account_id)
        if steam_id in (None, ""):
            return None
        text = str(steam_id)
        try:
            numeric = int(text)
        except ValueError:
            return text
        # Dota/OpenDota mostly use 32-bit account_id. GSI spectator payloads often include both;
        # when only Steam64 is available, normalize it so live/replay and post-match records align.
        if numeric > 76561197960265728:
            return str(numeric - 76561197960265728)
        return text
