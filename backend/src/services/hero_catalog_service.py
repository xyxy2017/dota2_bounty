from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error, request


class HeroCatalogService:
    def __init__(self, *, api_base: str, cache_path: Path, timeout_seconds: float = 12.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.cache_path = cache_path
        self.timeout_seconds = timeout_seconds
        self._heroes: dict[int, dict[str, str]] | None = None
        self._opener = request.build_opener(request.ProxyHandler({}))

    def resolve_name(self, hero_id: int | None) -> str | None:
        return self.resolve_name_display(hero_id)

    def resolve_name_zh(self, hero_id: int | None) -> str | None:
        if hero_id is None:
            return None
        self._ensure_loaded()
        if not self._heroes:
            return None
        return (self._heroes.get(int(hero_id)) or {}).get("zh")

    def resolve_name_en(self, hero_id: int | None) -> str | None:
        if hero_id is None:
            return None
        self._ensure_loaded()
        if not self._heroes:
            return None
        return (self._heroes.get(int(hero_id)) or {}).get("en")

    def resolve_name_display(self, hero_id: int | None) -> str | None:
        zh_name = self.resolve_name_zh(hero_id)
        en_name = self.resolve_name_en(hero_id)
        if zh_name and en_name:
            return f"{zh_name} ({en_name})"
        return zh_name or en_name

    def refresh(self) -> int:
        heroes = self._fetch_hero_catalog()
        if not heroes:
            return 0
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(
                [
                    {
                        "id": hero_id,
                        "name_zh": names.get("zh"),
                        "name_en": names.get("en"),
                    }
                    for hero_id, names in sorted(heroes.items())
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._heroes = heroes
        return len(heroes)

    def _ensure_loaded(self) -> None:
        if self._heroes is not None:
            return
        cached = self._read_cache()
        if cached:
            self._heroes = cached
            return
        try:
            self.refresh()
        except RuntimeError:
            self._heroes = {}

    def _read_cache(self) -> dict[int, dict[str, str]]:
        if not self.cache_path.exists():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return self._normalize(payload)

    def _fetch_remote(self, url: str) -> Any:
        req = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "dota2-bounty-backend/0.1 (+hero-catalog)",
            },
            method="GET",
        )
        try:
            with self._opener.open(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Hero catalog HTTP {exc.code}: {body[:200]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Hero catalog network error: {exc.reason}") from exc

    def _fetch_hero_catalog(self) -> dict[int, dict[str, str]]:
        zh_payload = self._fetch_remote("https://www.dota2.com/datafeed/herolist?language=schinese")
        en_payload = self._fetch_remote("https://www.dota2.com/datafeed/herolist?language=english")
        zh_heroes = self._normalize(zh_payload)
        en_heroes = self._normalize(en_payload)
        merged: dict[int, dict[str, str]] = {}
        for hero_id in set(zh_heroes.keys()) | set(en_heroes.keys()):
            zh_name = (zh_heroes.get(hero_id) or {}).get("zh")
            en_name = (en_heroes.get(hero_id) or {}).get("en")
            if not zh_name and not en_name:
                continue
            merged[hero_id] = {"zh": zh_name or en_name or "", "en": en_name or zh_name or ""}
        return merged

    def _normalize(self, payload: Any) -> dict[int, dict[str, str]]:
        # format A: dota2.com datafeed wrapper
        if isinstance(payload, dict):
            heroes = (((payload.get("result") or {}).get("data") or {}).get("heroes") or [])
        elif isinstance(payload, list):
            # format B: cached plain list
            heroes = payload
        else:
            heroes = []
        if not isinstance(heroes, list):
            return {}
        result: dict[int, dict[str, str]] = {}
        for item in heroes:
            if not isinstance(item, dict):
                continue
            hero_id = item.get("id")
            try:
                parsed_id = int(hero_id)
            except (TypeError, ValueError):
                continue
            zh_name = item.get("name_zh") or item.get("name_loc")
            en_name = item.get("name_en") or item.get("name_loc") or item.get("localized_name") or item.get("name")
            if not zh_name and not en_name:
                continue
            # Fallback: convert internal ids like npc_dota_hero_storm_spirit to "Storm Spirit".
            zh_text = _sanitize_internal_hero_name(str(zh_name)) if zh_name else ""
            en_text = _sanitize_internal_hero_name(str(en_name)) if en_name else ""
            result[parsed_id] = {
                "zh": zh_text,
                "en": en_text,
            }
        return result


def _sanitize_internal_hero_name(value: str) -> str:
    text = value.strip()
    prefix = "npc_dota_hero_"
    if text.startswith(prefix):
        raw = text[len(prefix) :]
        return " ".join(word.capitalize() for word in raw.split("_") if word)
    return text
