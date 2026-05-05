from __future__ import annotations

import json
import ssl
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

from domain.models import MatchResolveContext, ResolvedMatchResult, ResolvedPlayer

STEAM_ID_BASE = 76561197960265728


class OpenDotaResultProvider:
    name = "opendota"

    def __init__(self, api_base: str, timeout_seconds: float = 12.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener = request.build_opener(request.ProxyHandler({}))

    def is_available(self) -> bool:
        return True

    def resolve(self, ctx: MatchResolveContext) -> ResolvedMatchResult | None:
        if not ctx.match_id:
            return None
        match_json = self.fetch_match_details(ctx.match_id)
        if not match_json:
            return None
        return self._to_resolved_match(match_json, ctx)

    def fetch_recent_matches(self, account_id: str, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        payload = self._get_json(f"/players/{account_id}/recentMatches")
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)][:safe_limit]

    def fetch_player_matches(
        self,
        account_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        safe_offset = max(0, int(offset))
        payload = self._get_json(f"/players/{account_id}/matches?limit={safe_limit}&offset={safe_offset}")
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def fetch_match_details(self, match_id: str) -> dict[str, Any] | None:
        payload = self._get_json(f"/matches/{match_id}")
        if not isinstance(payload, dict):
            return None
        if payload.get("error"):
            return None
        return payload

    def _get_json(self, path: str) -> Any:
        url = f"{self.api_base}{path}"
        req = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "dota2-bounty-backend/0.1 (+local-dev)",
            },
            method="GET",
        )
        try:
            with self._opener.open(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OpenDota HTTP {exc.code}: {body[:300]}") from exc
        except ssl.SSLError as exc:
            raise RuntimeError(f"OpenDota SSL error: {exc}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"OpenDota network error: {exc.reason}") from exc

    def _to_resolved_match(self, match_json: dict[str, Any], ctx: MatchResolveContext) -> ResolvedMatchResult | None:
        raw_match_id = match_json.get("match_id") or ctx.match_id
        if not raw_match_id:
            return None
        match_id = str(raw_match_id)
        local_player_id = _normalize_account_id(ctx.local_player_id)
        local_team: str | None = None
        players: list[ResolvedPlayer] = []

        players_json = match_json.get("players", [])
        if isinstance(players_json, list):
            for row in players_json:
                if not isinstance(row, dict):
                    continue
                account_id = row.get("account_id")
                if account_id is None:
                    continue
                player_id = str(account_id)
                team = _team_from_slot(row.get("player_slot"))
                is_local = local_player_id is not None and player_id == local_player_id
                if is_local:
                    local_team = team
                players.append(
                    ResolvedPlayer(
                        player_id=player_id,
                        name=row.get("personaname"),
                        hero_id=_safe_int(row.get("hero_id")),
                        team=team,
                        is_local_player=is_local,
                    )
                )

        result = _resolve_local_result(radiant_win=match_json.get("radiant_win"), local_team=local_team)
        started_at = _to_iso(match_json.get("start_time"))
        ended_at = _to_ended_iso(match_json.get("start_time"), match_json.get("duration"))
        completeness = "completed" if players else "partial"
        return ResolvedMatchResult(
            match_id=match_id,
            temp_match_key=ctx.temp_match_key,
            local_player_id=local_player_id,
            result=result,
            players=players,
            started_at=started_at,
            ended_at=ended_at,
            source=self.name,
            completeness=completeness,
            raw_payload=match_json,
        )


def _normalize_account_id(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.isdigit():
        return text
    num = int(text)
    if num > STEAM_ID_BASE:
        return str(num - STEAM_ID_BASE)
    return str(num)


def _team_from_slot(player_slot: Any) -> str | None:
    slot = _safe_int(player_slot)
    if slot is None:
        return None
    return "radiant" if slot < 128 else "dire"


def _resolve_local_result(radiant_win: Any, local_team: str | None) -> str:
    if not isinstance(radiant_win, bool) or local_team not in {"radiant", "dire"}:
        return "unknown"
    if local_team == "radiant":
        return "win" if radiant_win else "lose"
    return "win" if not radiant_win else "lose"


def _to_iso(unix_ts: Any) -> str | None:
    ts = _safe_int(unix_ts)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _to_ended_iso(start_time: Any, duration: Any) -> str | None:
    start_ts = _safe_int(start_time)
    duration_sec = _safe_int(duration)
    if start_ts is None or duration_sec is None:
        return None
    return datetime.fromtimestamp(start_ts + duration_sec, tz=timezone.utc).isoformat()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
