from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.enums import DataStatus, SessionState


@dataclass
class SessionSnapshot:
    temp_match_key: str | None
    state: SessionState
    started_at: str | None
    last_seen_at: str | None
    map_name: str | None = None
    game_state: str | None = None


@dataclass
class NormalizedPlayer:
    player_id: str
    name: str | None = None
    hero_name: str | None = None
    hero_id: int | None = None
    team: str | None = None
    is_local_player: bool = False


@dataclass
class RosterSnapshot:
    temp_match_key: str | None
    collected_at: str
    players: list[NormalizedPlayer] = field(default_factory=list)
    completeness: str = "partial"
    source: str = "gsi"
    raw_payload: dict[str, Any] | None = None


@dataclass
class HistoricalHit:
    player_id: str
    latest_name: str | None
    encounter_count: int
    last_encounter_at: str | None
    last_result: str | None
    teammate_count: int = 0
    opponent_count: int = 0
    win_count: int = 0
    lose_count: int = 0
    last_match_id: str | None = None
    last_same_team: int | None = None
    last_player_hero_id: int | None = None
    last_my_hero_id: int | None = None
    last_player_hero_name: str | None = None
    last_my_hero_name: str | None = None
    last_player_hero_name_zh: str | None = None
    last_my_hero_name_zh: str | None = None
    last_player_hero_name_en: str | None = None
    last_my_hero_name_en: str | None = None
    last_relation: str | None = None
    priority_score: int = 0
    summary_text: str | None = None
    tag: str | None = None
    note: str | None = None


@dataclass
class EncounterUpsert:
    player_id: str
    player_name: str | None
    temp_match_key: str | None
    played_at: str | None
    data_status: DataStatus
    source: str = "gsi"


@dataclass
class ResolvedPlayer:
    player_id: str
    name: str | None = None
    hero_id: int | None = None
    team: str | None = None
    is_local_player: bool = False


@dataclass
class ResolvedMatchResult:
    match_id: str
    temp_match_key: str | None
    local_player_id: str | None
    result: str
    players: list[ResolvedPlayer]
    started_at: str | None = None
    ended_at: str | None = None
    source: str = "unknown"
    completeness: str = "partial"
    raw_payload: dict[str, Any] | None = None


@dataclass
class MatchResolveContext:
    temp_match_key: str | None
    match_id: str | None = None
    local_player_id: str | None = None
    manual_result: dict[str, Any] | None = None


@dataclass
class PlayerMetaUpdate:
    player_id: str
    tag: str | None = None
    note: str | None = None


@dataclass
class OperatorSummary:
    total_players: int
    total_encounters: int
    total_events: int
    recent_hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RepeatedPlayerSummary:
    player_id: str
    latest_name: str | None
    encounter_count: int
    teammate_count: int
    opponent_count: int
    win_count: int
    lose_count: int
    last_encounter_at: str | None
    match_ids: list[str] = field(default_factory=list)
