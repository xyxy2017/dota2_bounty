from __future__ import annotations

import json
from dataclasses import asdict

from domain.models import MatchResolveContext, ResolvedMatchResult
from providers.result.base import MatchResultProvider
from storage.repositories.encounters_repo import EncountersRepository
from storage.repositories.events_repo import EventsRepository
from storage.repositories.jobs_repo import JobsRepository
from storage.repositories.matches_repo import MatchesRepository
from storage.repositories.players_repo import PlayersRepository


class MatchResolveService:
    def __init__(
        self,
        providers: list[MatchResultProvider],
        jobs_repo: JobsRepository,
        matches_repo: MatchesRepository,
        encounters_repo: EncountersRepository,
        players_repo: PlayersRepository,
        events_repo: EventsRepository,
    ) -> None:
        self.providers = providers
        self.jobs_repo = jobs_repo
        self.matches_repo = matches_repo
        self.encounters_repo = encounters_repo
        self.players_repo = players_repo
        self.events_repo = events_repo

    def run_once(self, limit: int = 10) -> dict:
        jobs = self.jobs_repo.claim_ready("resolve_match", limit=limit)
        processed = 0
        resolved = 0
        failed = 0
        retried = 0
        for job in jobs:
            processed += 1
            try:
                payload = json.loads(job["payload_json"])
                context = MatchResolveContext(
                    temp_match_key=payload.get("temp_match_key"),
                    match_id=payload.get("match_id"),
                    local_player_id=payload.get("local_player_id"),
                    manual_result=payload.get("manual_result"),
                )
                result = self._resolve(context)
                if not result:
                    self.jobs_repo.mark_retry(job["id"], "no_provider_result", delay_seconds=30)
                    retried += 1
                    continue

                self._upsert_players_from_result(result)
                self.matches_repo.upsert_resolved_match(result)
                updated_rows = self.encounters_repo.apply_resolved_match(result)
                self.events_repo.append_event(
                    "match_result_resolved",
                    {
                        "job_id": job["id"],
                        "match_id": result.match_id,
                        "temp_match_key": result.temp_match_key,
                        "updated_encounters": updated_rows,
                        "provider": result.source,
                        "result": result.result,
                    },
                )
                self.jobs_repo.mark_done(job["id"])
                resolved += 1
            except Exception as exc:  # pragma: no cover - runtime safety
                self.jobs_repo.mark_retry(job["id"], str(exc), delay_seconds=60)
                self.events_repo.append_event(
                    "match_result_resolve_failed",
                    {"job_id": job["id"], "error": str(exc)},
                )
                failed += 1
        return {
            "claimed": len(jobs),
            "processed": processed,
            "resolved": resolved,
            "failed": failed,
            "retried": retried,
        }

    def resolve_now(self, ctx: MatchResolveContext) -> dict:
        result = self._resolve(ctx)
        if not result:
            return {"resolved": False}
        self._upsert_players_from_result(result)
        self.matches_repo.upsert_resolved_match(result)
        updated_rows = self.encounters_repo.apply_resolved_match(result)
        self.events_repo.append_event(
            "match_result_resolved_manual",
            {
                "match_id": result.match_id,
                "temp_match_key": result.temp_match_key,
                "updated_encounters": updated_rows,
                "provider": result.source,
            },
        )
        return {
            "resolved": True,
            "match_id": result.match_id,
            "temp_match_key": result.temp_match_key,
            "provider": result.source,
            "updated_encounters": updated_rows,
            "result": asdict(result),
        }

    def _resolve(self, ctx: MatchResolveContext) -> ResolvedMatchResult | None:
        for provider in self.providers:
            if not provider.is_available():
                continue
            result = provider.resolve(ctx)
            if result:
                return result
        return None

    def _upsert_players_from_result(self, result: ResolvedMatchResult) -> None:
        seen_at = result.ended_at or result.started_at
        for player in result.players:
            self.players_repo.upsert_seen_player(
                player_id=player.player_id,
                name=player.name,
                seen_at=seen_at,
            )
