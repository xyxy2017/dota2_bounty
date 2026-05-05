from __future__ import annotations

from datetime import datetime

from domain.enums import DataStatus
from domain.models import EncounterUpsert, HistoricalHit, RosterSnapshot
from services.hero_catalog_service import HeroCatalogService
from storage.repositories.encounters_repo import EncountersRepository
from storage.repositories.players_repo import PlayersRepository


class HistoryMatchService:
    def __init__(
        self,
        players_repo: PlayersRepository,
        encounters_repo: EncountersRepository,
        hero_catalog: HeroCatalogService,
    ) -> None:
        self.players_repo = players_repo
        self.encounters_repo = encounters_repo
        self.hero_catalog = hero_catalog
        self.default_data_status = DataStatus.PENDING

    def find_hits(self, roster: RosterSnapshot) -> list[HistoricalHit]:
        player_ids = [
            player.player_id
            for player in roster.players
            if player.player_id and not player.is_local_player
        ]
        if not player_ids:
            return []
        players = self.players_repo.get_by_ids(player_ids)
        snapshots = self.encounters_repo.list_history_snapshots_by_player_ids(player_ids)
        hits: list[HistoricalHit] = []
        for player_id in player_ids:
            player_record = players.get(player_id)
            if not player_record:
                continue
            snapshot = snapshots.get(player_id, {})
            hit = HistoricalHit(
                player_id=player_id,
                latest_name=player_record["latest_name"],
                encounter_count=int(snapshot.get("encounter_count") or 0),
                last_encounter_at=snapshot.get("last_encounter_at"),
                last_result=snapshot.get("last_result"),
                teammate_count=int(snapshot.get("teammate_count") or 0),
                opponent_count=int(snapshot.get("opponent_count") or 0),
                win_count=int(snapshot.get("win_count") or 0),
                lose_count=int(snapshot.get("lose_count") or 0),
                last_match_id=snapshot.get("last_match_id"),
                last_same_team=snapshot.get("last_same_team"),
                last_player_hero_id=snapshot.get("last_player_hero_id"),
                last_my_hero_id=snapshot.get("last_my_hero_id"),
                last_player_hero_name=self.hero_catalog.resolve_name(snapshot.get("last_player_hero_id")),
                last_my_hero_name=self.hero_catalog.resolve_name(snapshot.get("last_my_hero_id")),
                last_player_hero_name_zh=self.hero_catalog.resolve_name_zh(snapshot.get("last_player_hero_id")),
                last_my_hero_name_zh=self.hero_catalog.resolve_name_zh(snapshot.get("last_my_hero_id")),
                last_player_hero_name_en=self.hero_catalog.resolve_name_en(snapshot.get("last_player_hero_id")),
                last_my_hero_name_en=self.hero_catalog.resolve_name_en(snapshot.get("last_my_hero_id")),
                tag=player_record["tag"],
                note=player_record["note"],
            )
            hit.last_relation = _relation_from_same_team(hit.last_same_team)
            hit.priority_score = _priority_score(hit)
            hit.summary_text = _build_summary_text(hit)
            hits.append(hit)
        hits.sort(key=_sort_key, reverse=True)
        return hits

    def register_seen_player(self, encounter: EncounterUpsert) -> None:
        self.players_repo.upsert_seen_player(encounter.player_id, encounter.player_name, encounter.played_at)
        self.encounters_repo.upsert_pending_encounter(encounter)


def _relation_from_same_team(same_team: int | None) -> str | None:
    if same_team == 1:
        return "teammate"
    if same_team == 0:
        return "opponent"
    return None


def _priority_score(hit: HistoricalHit) -> int:
    score = hit.encounter_count * 10
    score += hit.win_count + hit.lose_count
    if hit.note:
        score += 30
    if hit.tag:
        score += _tag_weight(hit.tag)
    if hit.last_relation == "opponent":
        score += 5
    return score


def _tag_weight(tag: str) -> int:
    text = _normalize_tag(tag)
    if text in {"enemy", "rival", "toxic", "avoid", "仇人", "毒瘤", "避雷"}:
        return 100
    if text in {"friend", "ally", "good", "carry", "大腿", "靠谱队友"}:
        return 60
    if text in {"关注", "绝活哥"}:
        return 50
    return 40


def _sort_key(hit: HistoricalHit) -> tuple[int, str, int]:
    return (
        hit.priority_score,
        _sortable_datetime(hit.last_encounter_at),
        hit.encounter_count,
    )


def _sortable_datetime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return value


def _build_summary_text(hit: HistoricalHit) -> str:
    prefix = _tag_prefix(hit.tag)
    relation = _relation_text(hit.last_relation)
    their_hero = hit.last_player_hero_name or (
        f"英雄#{hit.last_player_hero_id}" if hit.last_player_hero_id is not None else None
    )
    my_hero = hit.last_my_hero_name or (
        f"英雄#{hit.last_my_hero_id}" if hit.last_my_hero_id is not None else None
    )
    result_text = _result_text(hit.last_result)

    primary_parts: list[str] = [relation]
    if their_hero:
        primary_parts.append(f"对方用{their_hero}")
    if my_hero:
        primary_parts.append(f"我用{my_hero}")
    if result_text:
        primary_parts.append(result_text)

    summary = "，".join(primary_parts)
    meta = f"累计{hit.encounter_count}次，{hit.teammate_count}次队友 / {hit.opponent_count}次对手"

    suffix_parts: list[str] = [meta]
    if hit.note:
        suffix_parts.append(f"备注：{hit.note}")

    text = f"{summary}；{ '；'.join(suffix_parts) }"
    if prefix:
        return f"{prefix} {text}"
    return text


def _normalize_tag(tag: str | None) -> str:
    if not tag:
        return ""
    return tag.strip().lower()


def _tag_prefix(tag: str | None) -> str | None:
    if not tag:
        return None
    normalized = _normalize_tag(tag)
    if normalized in {"enemy", "rival", "仇人"}:
        return "[仇人]"
    if normalized in {"toxic", "毒瘤"}:
        return "[毒瘤]"
    if normalized in {"avoid", "避雷"}:
        return "[避雷]"
    if normalized in {"关注"}:
        return "[关注]"
    if normalized in {"carry", "大腿"}:
        return "[大腿]"
    if normalized in {"ally", "friend", "靠谱队友"}:
        return "[靠谱队友]"
    if normalized in {"绝活哥"}:
        return "[绝活哥]"
    return f"[{tag}]"


def _relation_text(relation: str | None) -> str:
    if relation == "teammate":
        return "上次你们是队友"
    if relation == "opponent":
        return "上次你们是对手"
    return "上次关系未知"


def _result_text(result: str | None) -> str | None:
    if result == "win":
        return "那局你赢了"
    if result == "lose":
        return "那局你输了"
    if result:
        return f"结果{result}"
    return None
