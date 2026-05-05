from __future__ import annotations

from typing import Any

from domain.models import OperatorSummary
from services.hero_catalog_service import HeroCatalogService
from storage.repositories.encounters_repo import EncountersRepository
from storage.repositories.events_repo import EventsRepository
from storage.repositories.players_repo import PlayersRepository


class QueryService:
    def __init__(
        self,
        players_repo: PlayersRepository,
        encounters_repo: EncountersRepository,
        events_repo: EventsRepository,
        hero_catalog: HeroCatalogService,
    ) -> None:
        self.players_repo = players_repo
        self.encounters_repo = encounters_repo
        self.events_repo = events_repo
        self.hero_catalog = hero_catalog

    def list_recent_encounters(self, limit: int) -> list[dict[str, Any]]:
        return self._enrich_encounters(self.encounters_repo.list_recent_encounters(limit=limit))

    def list_encounters_for_player(self, player_id: str, limit: int) -> list[dict[str, Any]]:
        return self._enrich_encounters(
            self.encounters_repo.list_encounters_for_player(player_id=player_id, limit=limit)
        )

    def list_recent_events(self, limit: int, event_type: str | None = None) -> list[dict[str, Any]]:
        return self.events_repo.list_recent_events(limit=limit, event_type=event_type)

    def list_repeated_players(
        self,
        *,
        limit: int,
        min_encounters: int,
        exclude_player_id: str | None = None,
    ) -> list[dict[str, Any]]:
        items = self.encounters_repo.list_repeated_players(
            limit=limit,
            min_encounters=min_encounters,
            exclude_player_id=exclude_player_id,
        )
        return [
            {
                "player_id": item.player_id,
                "latest_name": item.latest_name,
                "encounter_count": item.encounter_count,
                "teammate_count": item.teammate_count,
                "opponent_count": item.opponent_count,
                "win_count": item.win_count,
                "lose_count": item.lose_count,
                "last_encounter_at": item.last_encounter_at,
                "match_ids": item.match_ids,
            }
            for item in items
        ]

    def get_player_history_summary(self, player_id: str, limit: int = 5) -> dict[str, Any] | None:
        player = self.players_repo.get_by_id(player_id)
        if not player:
            return None
        snapshot = self.encounters_repo.list_history_snapshots_by_player_ids([player_id]).get(player_id, {})
        recent = self._enrich_encounters(
            self.encounters_repo.list_completed_encounters_for_player(player_id=player_id, limit=limit)
        )
        return {
            "player": player,
            "summary": {
                "encounter_count": int(snapshot.get("encounter_count") or 0),
                "teammate_count": int(snapshot.get("teammate_count") or 0),
                "opponent_count": int(snapshot.get("opponent_count") or 0),
                "win_count": int(snapshot.get("win_count") or 0),
                "lose_count": int(snapshot.get("lose_count") or 0),
                "last_encounter_at": snapshot.get("last_encounter_at"),
                "last_result": snapshot.get("last_result"),
                "last_match_id": snapshot.get("last_match_id"),
                "last_same_team": snapshot.get("last_same_team"),
                "last_player_hero_id": snapshot.get("last_player_hero_id"),
                "last_my_hero_id": snapshot.get("last_my_hero_id"),
                "last_player_hero_name": self.hero_catalog.resolve_name(snapshot.get("last_player_hero_id")),
                "last_my_hero_name": self.hero_catalog.resolve_name(snapshot.get("last_my_hero_id")),
                "last_player_hero_name_zh": self.hero_catalog.resolve_name_zh(snapshot.get("last_player_hero_id")),
                "last_my_hero_name_zh": self.hero_catalog.resolve_name_zh(snapshot.get("last_my_hero_id")),
                "last_player_hero_name_en": self.hero_catalog.resolve_name_en(snapshot.get("last_player_hero_id")),
                "last_my_hero_name_en": self.hero_catalog.resolve_name_en(snapshot.get("last_my_hero_id")),
            },
            "recent_encounters": recent,
        }

    def build_summary(self) -> OperatorSummary:
        recent_hits = self.events_repo.list_recent_events(limit=10, event_type="historical_player_hit")
        return OperatorSummary(
            total_players=self.players_repo.count_all(),
            total_encounters=self.encounters_repo.count_all(),
            total_events=self.events_repo.count_all(),
            recent_hits=recent_hits,
        )

    def _enrich_encounters(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for item in items:
            row = dict(item)
            player_hero_id = row.get("player_hero_id")
            my_hero_id = row.get("my_hero_id")
            row["player_hero_name"] = self.hero_catalog.resolve_name(player_hero_id)
            row["my_hero_name"] = self.hero_catalog.resolve_name(my_hero_id)
            row["player_hero_name_zh"] = self.hero_catalog.resolve_name_zh(player_hero_id)
            row["my_hero_name_zh"] = self.hero_catalog.resolve_name_zh(my_hero_id)
            row["player_hero_name_en"] = self.hero_catalog.resolve_name_en(player_hero_id)
            row["my_hero_name_en"] = self.hero_catalog.resolve_name_en(my_hero_id)
            enriched.append(row)
        return enriched
