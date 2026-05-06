from __future__ import annotations

import threading
from datetime import datetime, timezone

from config.settings import Settings
from domain.models import MatchResolveContext, PlayerMetaUpdate
from infra.logger import configure_logging, get_logger
from infra.process_lock import ProcessLock
from infra.runtime_status import RuntimeStatusWriter
from providers.result.mock_payload_provider import MockPayloadResultProvider
from providers.result.noop_provider import NoopResultProvider
from providers.result.open_dota_provider import OpenDotaResultProvider
from services.backfill_service import OpenDotaBackfillService
from services.gsi_ingest_service import GsiIngestService
from services.hero_catalog_service import HeroCatalogService
from services.history_match_service import HistoryMatchService
from services.job_service import JobService
from services.match_resolve_service import MatchResolveService
from services.ocr_match_service import OcrMatchService
from services.operator_service import OperatorService
from services.query_service import QueryService
from services.roster_collect_service import RosterCollectService
from services.session_tracker import SessionTracker
from storage.db import Database
from storage.repositories.encounters_repo import EncountersRepository
from storage.repositories.events_repo import EventsRepository
from storage.repositories.jobs_repo import JobsRepository
from storage.repositories.matches_repo import MatchesRepository
from storage.repositories.players_repo import PlayersRepository


class HttpError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Supervisor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process_lock = ProcessLock(settings.lock_file_path)
        self.runtime_status = RuntimeStatusWriter(settings.runtime_status_path)
        self.alerts_writer = RuntimeStatusWriter(settings.alerts_path)
        self.database = Database(settings.database_path)
        self.players_repo = PlayersRepository(self.database)
        self.encounters_repo = EncountersRepository(self.database)
        self.events_repo = EventsRepository(self.database)
        self.jobs_repo = JobsRepository(self.database)
        self.matches_repo = MatchesRepository(self.database)
        self.hero_catalog = HeroCatalogService(
            api_base=settings.opendota_api_base,
            cache_path=settings.hero_catalog_path,
            timeout_seconds=settings.opendota_timeout_seconds,
        )
        self.session_tracker = SessionTracker()
        self.roster_collector = RosterCollectService()
        self.job_service = JobService(
            self.jobs_repo,
            self.session_tracker,
            default_local_player_id=settings.default_account_id,
        )
        self.opendota_provider = OpenDotaResultProvider(
            api_base=settings.opendota_api_base,
            timeout_seconds=settings.opendota_timeout_seconds,
        )
        self.match_resolver = MatchResolveService(
            providers=[MockPayloadResultProvider(), self.opendota_provider, NoopResultProvider()],
            jobs_repo=self.jobs_repo,
            matches_repo=self.matches_repo,
            encounters_repo=self.encounters_repo,
            players_repo=self.players_repo,
            events_repo=self.events_repo,
        )
        self.history_matcher = HistoryMatchService(
            players_repo=self.players_repo,
            encounters_repo=self.encounters_repo,
            hero_catalog=self.hero_catalog,
        )
        self.gsi_ingest = GsiIngestService(
            events_repo=self.events_repo,
            session_tracker=self.session_tracker,
            roster_collector=self.roster_collector,
            history_matcher=self.history_matcher,
            job_service=self.job_service,
            runtime_status=self.runtime_status,
            alerts_writer=self.alerts_writer,
            exclude_player_id=settings.default_account_id,
        )
        self.operator_service = OperatorService(players_repo=self.players_repo)
        self.ocr_match_service = OcrMatchService(players_repo=self.players_repo)
        self.query_service = QueryService(
            players_repo=self.players_repo,
            encounters_repo=self.encounters_repo,
            events_repo=self.events_repo,
            hero_catalog=self.hero_catalog,
        )
        self.backfill_service = OpenDotaBackfillService(
            provider=self.opendota_provider,
            job_service=self.job_service,
            match_resolver=self.match_resolver,
            events_repo=self.events_repo,
            default_limit=settings.opendota_backfill_default_limit,
        )
        configure_logging(settings.log_level, settings.log_file_path)
        self.logger = get_logger("supervisor")
        self._worker_stop = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def startup(self) -> None:
        if not self.process_lock.acquire():
            raise RuntimeError("Backend process is already running.")
        self.database.initialize()
        try:
            self.hero_catalog.refresh()
        except RuntimeError:
            pass
        self.runtime_status.write(
            {
                "state": "starting",
                "server": {
                    "host": self.settings.server_host,
                    "port": self.settings.server_port,
                },
            }
        )
        self.alerts_writer.write({"hit_count": 0, "headline": None, "items": []})
        self._start_background_worker()
        self.logger.info("backend_started")

    def shutdown(self) -> None:
        self._stop_background_worker()
        self.runtime_status.write({"state": "stopped"})
        self.alerts_writer.write({"hit_count": 0, "headline": None, "items": []})
        self.process_lock.release()
        self.logger.info("backend_stopped")

    def _start_background_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_stop.clear()
        self._worker_thread = threading.Thread(
            target=self._background_worker_loop,
            name="match-resolve-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def _stop_background_worker(self) -> None:
        self._worker_stop.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2)

    def _background_worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            try:
                result = self.match_resolver.run_once(limit=5)
                if result.get("processed"):
                    self.runtime_status.merge({"last_resolve_worker_result": result})
            except Exception:  # pragma: no cover - defensive worker isolation
                self.logger.exception("resolve_worker_failed")
            self._worker_stop.wait(30)

    def handle_request(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        payload: dict | None,
    ) -> tuple[int, dict]:
        try:
            if method == "GET" and path == "/health":
                return 200, {"ok": True}

            if method == "GET" and path == "/status":
                return 200, self.runtime_status.read()

            if method == "GET" and path == "/debug/roster":
                status = self.runtime_status.read()
                return 200, {
                    "temp_match_key": status.get("temp_match_key"),
                    "state": status.get("state"),
                    "roster_size": status.get("roster_size", 0),
                    "roster_completeness": status.get("roster_completeness"),
                    "roster_players": status.get("roster_players", []),
                    "last_payload_keys": status.get("last_payload_keys", []),
                    "updated_at": status.get("updated_at"),
                }

            if method == "GET" and path == "/alerts/current":
                return 200, self.alerts_writer.read()

            if method == "POST" and path == "/ocr/match":
                data = payload or {}
                threshold = _to_bounded_float(data.get("threshold"), 0.72, 0.1, 1.0)
                result = self.ocr_match_service.match_text(
                    raw_text=data.get("raw_text"),
                    lines=data.get("lines") if isinstance(data.get("lines"), list) else None,
                    threshold=threshold,
                    exclude_player_id=str(data.get("exclude_player_id") or self.settings.default_account_id or ""),
                )
                self.events_repo.append_event(
                    "ocr_roster_matched",
                    {
                        "match_count": result["match_count"],
                        "extracted_line_count": len(result["extracted_lines"]),
                        "threshold": threshold,
                        "published_alerts": bool(data.get("publish_alerts") and result.get("matches")),
                    },
                )
                self.runtime_status.merge({"last_ocr_match": result})
                if data.get("publish_alerts") and result.get("matches"):
                    alerts_payload = _build_ocr_alerts_payload(result)
                    self.alerts_writer.write(alerts_payload)
                    self.events_repo.append_event("ocr_alerts_published", alerts_payload)
                return 200, result

            if method == "GET" and path == "/summary":
                result = self.query_service.build_summary()
                body = {
                    "total_players": result.total_players,
                    "total_encounters": result.total_encounters,
                    "total_events": result.total_events,
                    "recent_hits": result.recent_hits,
                }
                self.runtime_status.merge({"summary": body})
                return 200, body

            if method == "POST" and path in {"/gsi", "/gsi/"}:
                return 200, self.gsi_ingest.handle(payload or {})

            if method == "POST" and path == "/resolve/now":
                context = MatchResolveContext(
                    temp_match_key=(payload or {}).get("temp_match_key"),
                    match_id=(payload or {}).get("match_id"),
                    local_player_id=(payload or {}).get("local_player_id") or self.settings.default_account_id,
                    manual_result=(payload or {}).get("manual_result"),
                )
                return 200, self.match_resolver.resolve_now(context)

            if method == "POST" and path == "/resolve/enqueue":
                job_id = self.job_service.enqueue_match_resolution(
                    temp_match_key=(payload or {}).get("temp_match_key"),
                    match_id=(payload or {}).get("match_id"),
                    local_player_id=(payload or {}).get("local_player_id") or self.settings.default_account_id,
                    manual_result=(payload or {}).get("manual_result"),
                )
                return 200, {"job_id": job_id}

            if method == "POST" and path == "/resolve/jobs":
                limit = _to_bounded_int((payload or {}).get("limit"), 10, 1, 500)
                return 200, self.match_resolver.run_once(limit=limit)

            if method == "GET" and path == "/resolve/jobs":
                limit = _to_bounded_int(query.get("limit"), 50, 1, 500)
                return 200, {"items": self.jobs_repo.list_recent(limit=limit)}

            if method == "GET" and path == "/players/recent":
                limit = _to_bounded_int(query.get("limit"), 20, 1, 200)
                exclude_player_id = query.get("exclude_player_id") or self.settings.default_account_id
                return 200, {
                    "items": self.operator_service.list_recent_players(
                        limit=limit,
                        exclude_player_id=exclude_player_id,
                    )
                }

            if method == "GET" and path == "/tags/presets":
                return 200, {"items": self.operator_service.list_tag_presets()}

            if method == "GET" and path == "/players/repeats":
                limit = _to_bounded_int(query.get("limit"), 20, 1, 200)
                min_encounters = _to_bounded_int(query.get("min_encounters"), 2, 2, 100)
                exclude_player_id = query.get("exclude_player_id") or self.settings.default_account_id
                return 200, {
                    "items": self.query_service.list_repeated_players(
                        limit=limit,
                        min_encounters=min_encounters,
                        exclude_player_id=exclude_player_id,
                    )
                }

            if method == "GET" and path.startswith("/players/") and path.endswith("/history-summary"):
                player_id = path.removeprefix("/players/").removesuffix("/history-summary").rstrip("/")
                if not player_id:
                    raise HttpError(404, "player not found")
                limit = _to_bounded_int(query.get("limit"), 5, 1, 50)
                summary = self.query_service.get_player_history_summary(player_id=player_id, limit=limit)
                if not summary:
                    raise HttpError(404, "player not found")
                return 200, summary

            if path.startswith("/players/"):
                player_id = path.removeprefix("/players/")
                if not player_id:
                    raise HttpError(404, "player not found")

                if method == "GET":
                    player = self.operator_service.get_player(player_id)
                    if not player:
                        raise HttpError(404, "player not found")
                    return 200, player

                if method == "PATCH":
                    data = payload or {}
                    player = self.operator_service.update_player_meta(
                        update=PlayerMetaUpdate(
                            player_id=player_id,
                            tag=data.get("tag"),
                            note=data.get("note"),
                        )
                    )
                    if not player:
                        raise HttpError(404, "player not found")
                    self.events_repo.append_event(
                        "player_meta_updated",
                        {"player_id": player_id, "tag": data.get("tag"), "note": data.get("note")},
                    )
                    return 200, player

            if method == "GET" and path == "/encounters/recent":
                limit = _to_bounded_int(query.get("limit"), 50, 1, 500)
                return 200, {"items": self.query_service.list_recent_encounters(limit=limit)}

            if method == "GET" and path.startswith("/encounters/player/"):
                player_id = path.removeprefix("/encounters/player/")
                limit = _to_bounded_int(query.get("limit"), 50, 1, 500)
                return 200, {
                    "items": self.query_service.list_encounters_for_player(player_id=player_id, limit=limit)
                }

            if method == "GET" and path == "/events/recent":
                limit = _to_bounded_int(query.get("limit"), 100, 1, 1000)
                event_type = query.get("event_type")
                return 200, {"items": self.query_service.list_recent_events(limit=limit, event_type=event_type)}

            if method == "GET" and path == "/backfill/recent":
                account_id = _pick_account_id(
                    query.get("account_id"),
                    self.settings.default_account_id,
                )
                limit = _to_bounded_int(query.get("limit"), self.settings.opendota_backfill_default_limit, 1, 100)
                return 200, self.backfill_service.preview_recent_matches(account_id=account_id, limit=limit)

            if method == "POST" and path == "/backfill/enqueue":
                data = payload or {}
                account_id = _pick_account_id(
                    data.get("account_id"),
                    self.settings.default_account_id,
                )
                limit = _to_bounded_int(data.get("limit"), self.settings.opendota_backfill_default_limit, 1, 100)
                return 200, self.backfill_service.enqueue_recent_matches(account_id=account_id, limit=limit)

            if method == "POST" and path == "/backfill/resolve":
                data = payload or {}
                account_id = _pick_account_id(
                    data.get("account_id"),
                    self.settings.default_account_id,
                )
                limit = _to_bounded_int(data.get("limit"), self.settings.opendota_backfill_default_limit, 1, 100)
                return 200, self.backfill_service.resolve_recent_matches_now(account_id=account_id, limit=limit)

            raise HttpError(404, "not found")
        except HttpError:
            raise
        except Exception as exc:  # pragma: no cover - runtime safety
            self.logger.exception("request_failed")
            raise HttpError(500, str(exc)) from exc


def _to_bounded_int(value: str | int | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _to_bounded_float(value: str | int | float | None, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _pick_account_id(candidate: str | int | None, fallback: str | None) -> str:
    value = candidate if candidate not in (None, "") else fallback
    if value in (None, ""):
        raise HttpError(400, "account_id is required")
    return str(value)


def _build_ocr_alerts_payload(match_result: dict) -> dict:
    matches = match_result.get("matches") or []
    items = []
    for item in matches[:3]:
        player = item.get("player") or {}
        name = item.get("matched_name") or player.get("latest_name") or item.get("matched_player_id")
        confidence = item.get("confidence")
        encounter_count = int(player.get("encounter_count") or 0)
        tag = player.get("tag")
        note = player.get("note")
        summary_parts = [
            f"OCR 疑似命中，置信度 {confidence}",
            f"历史记录 {encounter_count} 次",
            "盘中 OCR 只能按昵称弱匹配，赛后仍以真实 ID 补全为准",
        ]
        if note:
            summary_parts.append(f"备注：{note}")
        items.append(
            {
                "player_id": item.get("matched_player_id"),
                "player_name": name,
                "tag": tag,
                "note": note,
                "priority_score": int(round(float(confidence or 0) * 100)) + encounter_count,
                "summary_text": "；".join(summary_parts),
                "last_match_id": None,
                "last_result": None,
                "last_relation": None,
                "last_player_hero_name": None,
                "last_my_hero_name": None,
                "last_player_hero_name_zh": None,
                "last_my_hero_name_zh": None,
                "last_player_hero_name_en": None,
                "last_my_hero_name_en": None,
                "last_player_hero_id": None,
                "last_my_hero_id": None,
                "encounter_count": encounter_count,
                "match_method": item.get("match_method"),
                "confidence": confidence,
                "confirmed_id": False,
                "source": "ocr",
            }
        )
    top_name = items[0]["player_name"] if items else None
    headline = f"OCR 疑似命中: {top_name}" if top_name else None
    return {
        "temp_match_key": "ocr-live",
        "hit_count": len(matches),
        "headline": headline,
        "items": items,
        "source": "ocr",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
