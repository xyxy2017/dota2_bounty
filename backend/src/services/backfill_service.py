from __future__ import annotations

from domain.models import MatchResolveContext
from providers.result.open_dota_provider import OpenDotaResultProvider
from services.job_service import JobService
from services.match_resolve_service import MatchResolveService
from storage.repositories.events_repo import EventsRepository


class OpenDotaBackfillService:
    def __init__(
        self,
        provider: OpenDotaResultProvider,
        job_service: JobService,
        match_resolver: MatchResolveService,
        events_repo: EventsRepository,
        default_limit: int = 20,
    ) -> None:
        self.provider = provider
        self.job_service = job_service
        self.match_resolver = match_resolver
        self.events_repo = events_repo
        self.default_limit = default_limit

    def preview_recent_matches(self, account_id: str, limit: int | None = None) -> dict:
        safe_limit = _safe_limit(limit, self.default_limit)
        matches = self._fetch_target_matches(account_id=account_id, limit=safe_limit)
        items = [
            {
                "match_id": str(m.get("match_id")),
                "hero_id": m.get("hero_id"),
                "radiant_win": m.get("radiant_win"),
                "player_slot": m.get("player_slot"),
                "start_time": m.get("start_time"),
                "duration": m.get("duration"),
            }
            for m in matches
            if m.get("match_id") is not None
        ]
        return {"account_id": account_id, "limit": safe_limit, "count": len(items), "items": items}

    def enqueue_recent_matches(self, account_id: str, limit: int | None = None) -> dict:
        safe_limit = _safe_limit(limit, self.default_limit)
        matches = self._fetch_target_matches(account_id=account_id, limit=safe_limit)
        enqueued = 0
        job_ids: list[str] = []
        for match in matches:
            match_id = match.get("match_id")
            if match_id is None:
                continue
            match_id_text = str(match_id)
            temp_match_key = f"backfill-{account_id}-{match_id_text}"
            job_id = self.job_service.enqueue_match_resolution(
                temp_match_key=temp_match_key,
                match_id=match_id_text,
                local_player_id=account_id,
            )
            if job_id:
                enqueued += 1
                job_ids.append(job_id)
        result = {
            "account_id": account_id,
            "limit": safe_limit,
            "fetched": len(matches),
            "enqueued": enqueued,
            "job_ids": job_ids,
        }
        self.events_repo.append_event("backfill_enqueued", result)
        return result

    def resolve_recent_matches_now(self, account_id: str, limit: int | None = None) -> dict:
        safe_limit = _safe_limit(limit, self.default_limit)
        matches = self._fetch_target_matches(account_id=account_id, limit=safe_limit)
        resolved = 0
        unresolved = 0
        failed = 0
        details: list[dict] = []
        for match in matches:
            match_id = match.get("match_id")
            if match_id is None:
                continue
            match_id_text = str(match_id)
            temp_match_key = f"backfill-{account_id}-{match_id_text}"
            try:
                output = self.match_resolver.resolve_now(
                    MatchResolveContext(
                        temp_match_key=temp_match_key,
                        match_id=match_id_text,
                        local_player_id=account_id,
                    )
                )
                if output.get("resolved"):
                    resolved += 1
                else:
                    unresolved += 1
                details.append(
                    {
                        "match_id": match_id_text,
                        "resolved": bool(output.get("resolved")),
                        "updated_encounters": output.get("updated_encounters", 0),
                        "provider": output.get("provider"),
                    }
                )
            except Exception as exc:  # pragma: no cover - runtime/network safety
                failed += 1
                details.append(
                    {
                        "match_id": match_id_text,
                        "resolved": False,
                        "updated_encounters": 0,
                        "provider": None,
                        "error": str(exc),
                    }
                )
                self.events_repo.append_event(
                    "backfill_match_failed",
                    {"account_id": account_id, "match_id": match_id_text, "error": str(exc)},
                )

        result = {
            "account_id": account_id,
            "limit": safe_limit,
            "fetched": len(matches),
            "resolved": resolved,
            "unresolved": unresolved,
            "failed": failed,
            "details": details,
        }
        self.events_repo.append_event("backfill_resolved", result)
        return result

    def _fetch_target_matches(self, account_id: str, limit: int) -> list[dict]:
        if limit <= 20:
            return self.provider.fetch_recent_matches(account_id=account_id, limit=limit)

        remaining = limit
        offset = 0
        matches: list[dict] = []
        while remaining > 0:
            batch_size = min(remaining, 100)
            batch = self.provider.fetch_player_matches(
                account_id=account_id,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break
            matches.extend(batch)
            if len(batch) < batch_size:
                break
            remaining -= len(batch)
            offset += len(batch)
        return matches[:limit]


def _safe_limit(limit: int | None, fallback: int) -> int:
    if limit is None:
        return max(1, min(100, int(fallback)))
    return max(1, min(100, int(limit)))
