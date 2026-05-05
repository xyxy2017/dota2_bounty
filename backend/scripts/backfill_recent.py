from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import Settings
from providers.result.mock_payload_provider import MockPayloadResultProvider
from providers.result.noop_provider import NoopResultProvider
from providers.result.open_dota_provider import OpenDotaResultProvider
from services.backfill_service import OpenDotaBackfillService
from services.job_service import JobService
from services.match_resolve_service import MatchResolveService
from services.session_tracker import SessionTracker
from storage.db import Database
from storage.repositories.encounters_repo import EncountersRepository
from storage.repositories.events_repo import EventsRepository
from storage.repositories.jobs_repo import JobsRepository
from storage.repositories.matches_repo import MatchesRepository
from storage.repositories.players_repo import PlayersRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenDota historical backfill utility")
    parser.add_argument("--account-id", required=False, help="Dota account_id, e.g. 126600075")
    parser.add_argument("--limit", type=int, default=20, help="Recent match count to fetch (1-100)")
    parser.add_argument(
        "--mode",
        choices=["preview", "enqueue", "resolve", "enqueue-and-run"],
        default="resolve",
        help="preview only, enqueue jobs, resolve now, or enqueue then run jobs",
    )
    args = parser.parse_args()

    settings = Settings()
    account_id = args.account_id or settings.default_account_id
    if not account_id:
        raise SystemExit("account_id is required. Use --account-id or set DOTA2_BOUNTY_ACCOUNT_ID")

    db = Database(settings.database_path)
    db.initialize()
    events_repo = EventsRepository(db)
    jobs_repo = JobsRepository(db)
    matches_repo = MatchesRepository(db)
    encounters_repo = EncountersRepository(db)
    players_repo = PlayersRepository(db)

    job_service = JobService(jobs_repo=jobs_repo, session_tracker=SessionTracker())
    provider = OpenDotaResultProvider(
        api_base=settings.opendota_api_base,
        timeout_seconds=settings.opendota_timeout_seconds,
    )
    resolver = MatchResolveService(
        providers=[MockPayloadResultProvider(), provider, NoopResultProvider()],
        jobs_repo=jobs_repo,
        matches_repo=matches_repo,
        encounters_repo=encounters_repo,
        players_repo=players_repo,
        events_repo=events_repo,
    )
    backfill = OpenDotaBackfillService(
        provider=provider,
        job_service=job_service,
        match_resolver=resolver,
        events_repo=events_repo,
        default_limit=settings.opendota_backfill_default_limit,
    )

    try:
        if args.mode == "preview":
            result = backfill.preview_recent_matches(account_id=str(account_id), limit=args.limit)
        elif args.mode == "enqueue":
            result = backfill.enqueue_recent_matches(account_id=str(account_id), limit=args.limit)
        elif args.mode == "enqueue-and-run":
            enqueued = backfill.enqueue_recent_matches(account_id=str(account_id), limit=args.limit)
            ran = resolver.run_once(limit=max(1, args.limit))
            result = {"enqueue": enqueued, "run_jobs": ran}
        else:
            result = backfill.resolve_recent_matches_now(account_id=str(account_id), limit=args.limit)
    except Exception as exc:
        result = {
            "ok": False,
            "mode": args.mode,
            "account_id": str(account_id),
            "error": str(exc),
        }

    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
