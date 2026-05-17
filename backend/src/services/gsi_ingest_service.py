from __future__ import annotations

from typing import Any

from domain.models import EncounterUpsert
from infra.runtime_status import RuntimeStatusWriter
from services.history_match_service import HistoryMatchService
from services.job_service import JobService
from services.roster_collect_service import RosterCollectService
from services.session_tracker import SessionTracker
from services.gsi_id_probe_service import GsiIdProbeService
from storage.repositories.events_repo import EventsRepository


class GsiIngestService:
    def __init__(
        self,
        events_repo: EventsRepository,
        session_tracker: SessionTracker,
        roster_collector: RosterCollectService,
        history_matcher: HistoryMatchService,
        job_service: JobService,
        runtime_status: RuntimeStatusWriter,
        alerts_writer: RuntimeStatusWriter,
        exclude_player_id: str | None = None,
        id_probe: GsiIdProbeService | None = None,
    ) -> None:
        self.events_repo = events_repo
        self.session_tracker = session_tracker
        self.roster_collector = roster_collector
        self.history_matcher = history_matcher
        self.job_service = job_service
        self.runtime_status = runtime_status
        self.alerts_writer = alerts_writer
        self.exclude_player_id = str(exclude_player_id) if exclude_player_id else None
        self.id_probe = id_probe

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = self.events_repo.append_event("raw_gsi_event", payload)
        id_probe_status = self.id_probe.analyze(payload) if self.id_probe else None
        session = self.session_tracker.update(payload)
        roster = self.roster_collector.collect(payload, session)
        hits = self.history_matcher.find_hits(roster)
        hits = [hit for hit in hits if hit.player_id != self.exclude_player_id]

        for player in roster.players:
            if player.player_id:
                self.history_matcher.register_seen_player(
                    EncounterUpsert(
                        player_id=player.player_id,
                        player_name=player.name,
                        temp_match_key=roster.temp_match_key,
                        played_at=roster.collected_at,
                        data_status=self.history_matcher.default_data_status,
                    )
                )
        job_id = self.job_service.enqueue_from_gsi_payload(payload)

        status = {
            "state": session.state.value,
            "temp_match_key": session.temp_match_key,
            "event_id": event_id,
            "resolve_job_id": job_id,
            "roster_size": len(roster.players),
            "roster_completeness": roster.completeness,
            "roster_players": [
                {
                    "player_id": player.player_id,
                    "name": player.name,
                    "team": player.team,
                    "hero_id": player.hero_id,
                    "hero_name": player.hero_name,
                    "is_local_player": player.is_local_player,
                }
                for player in roster.players
            ],
            "hit_count": len(hits),
            "hits": [hit.__dict__ for hit in hits],
            "top_hits": [hit.__dict__ for hit in hits[:3]],
            "last_payload_keys": sorted(payload.keys()),
            "id_probe": id_probe_status,
        }
        self.runtime_status.write(status)
        alerts_payload = self._build_alerts_payload(session.temp_match_key, hits)
        self.alerts_writer.write(alerts_payload)
        self.events_repo.append_event(
            "roster_collected",
            {
                "temp_match_key": roster.temp_match_key,
                "roster_size": len(roster.players),
                "completeness": roster.completeness,
            },
        )
        if hits:
            self.events_repo.append_event("historical_player_hit", status)
            self.events_repo.append_event("alerts_updated", alerts_payload)
        if id_probe_status:
            self.events_repo.append_event(
                "gsi_id_probe_analyzed",
                {
                    "phase": id_probe_status.get("phase"),
                    "match_id": id_probe_status.get("match_id"),
                    "unique_player_id_count": id_probe_status.get("unique_player_id_count"),
                    "non_local_player_id_count": id_probe_status.get("non_local_player_id_count"),
                    "has_direct_other_player_ids": id_probe_status.get("has_direct_other_player_ids"),
                    "has_roster_like_payload": id_probe_status.get("has_roster_like_payload"),
                    "conclusion": id_probe_status.get("conclusion"),
                    "raw_payload_path": id_probe_status.get("raw_payload_path"),
                },
            )
        return status

    def _build_alerts_payload(self, temp_match_key: str | None, hits: list[Any]) -> dict[str, Any]:
        items = [
            {
                "player_id": hit.player_id,
                "player_name": hit.latest_name,
                "tag": hit.tag,
                "note": hit.note,
                "priority_score": hit.priority_score,
                "summary_text": hit.summary_text,
                "last_match_id": hit.last_match_id,
                "last_result": hit.last_result,
                "last_relation": hit.last_relation,
                "last_player_hero_name": hit.last_player_hero_name,
                "last_my_hero_name": hit.last_my_hero_name,
                "last_player_hero_name_zh": hit.last_player_hero_name_zh,
                "last_my_hero_name_zh": hit.last_my_hero_name_zh,
                "last_player_hero_name_en": hit.last_player_hero_name_en,
                "last_my_hero_name_en": hit.last_my_hero_name_en,
                "last_player_hero_id": hit.last_player_hero_id,
                "last_my_hero_id": hit.last_my_hero_id,
                "encounter_count": hit.encounter_count,
            }
            for hit in hits[:3]
        ]
        headline = None
        if items:
            top_item = items[0]
            top_name = top_item["player_name"] or top_item["player_id"]
            if top_item.get("tag") in {"仇人", "毒瘤", "避雷", "enemy", "rival", "toxic", "avoid"}:
                headline = f"高优提醒: {top_name}"
            elif top_item.get("tag") in {"大腿", "靠谱队友", "carry", "ally", "friend"}:
                headline = f"重点队友: {top_name}"
            else:
                names = " / ".join(item["player_name"] or item["player_id"] for item in items[:2])
                headline = f"命中{len(items)}名历史玩家: {names}"
        return {
            "temp_match_key": temp_match_key,
            "hit_count": len(hits),
            "headline": headline,
            "items": items,
        }
