from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from storage.repositories.players_repo import PlayersRepository


class OcrMatchService:
    def __init__(self, players_repo: PlayersRepository) -> None:
        self.players_repo = players_repo

    def match_text(
        self,
        *,
        raw_text: str | None = None,
        lines: list[str] | None = None,
        threshold: float = 0.72,
        candidate_limit: int = 1000,
        exclude_player_id: str | None = None,
    ) -> dict[str, Any]:
        extracted_lines = _extract_lines(raw_text=raw_text, lines=lines)
        candidates = self.players_repo.list_name_match_candidates(limit=candidate_limit)
        if exclude_player_id:
            candidates = [
                candidate
                for candidate in candidates
                if not _same_dota_player_id(candidate.get("player_id"), exclude_player_id)
            ]
        matches: list[dict[str, Any]] = []
        unmatched_lines: list[str] = []
        used_player_keys: set[str] = set()

        for line in extracted_lines:
            line_matches = _matches_for_line(line, candidates, threshold=threshold)
            if not line_matches:
                unmatched_lines.append(line)
                continue
            for best in line_matches:
                player_id = _canonical_dota_player_id(best["player"]["player_id"])
                player_key = _canonical_dota_player_id(player_id)
                duplicate = player_key in used_player_keys
                used_player_keys.add(player_key)
                matches.append(
                    {
                        "ocr_line": line,
                        "matched_player_id": player_id,
                        "matched_name": best["matched_name"],
                        "latest_name": best["player"].get("latest_name"),
                        "confidence": best["confidence"],
                        "match_method": best["method"],
                        "confirmed_id": False,
                        "duplicate_player_match": duplicate,
                        "player": {
                            "player_id": player_id,
                            "latest_name": best["player"].get("latest_name"),
                            "tag": best["player"].get("tag"),
                            "note": best["player"].get("note"),
                            "encounter_count": int(best["player"].get("encounter_count") or 0),
                            "last_seen_at": best["player"].get("last_seen_at"),
                        },
                    }
                )

        matches.sort(key=lambda item: (item["confidence"], item["player"]["encounter_count"]), reverse=True)
        return {
            "source": "ocr_text_match",
            "confirmed_id": False,
            "extracted_lines": extracted_lines,
            "match_count": len(matches),
            "matches": matches,
            "unmatched_lines": unmatched_lines,
            "threshold": threshold,
            "candidate_count": len(candidates),
            "note": "OCR only matches visible names to historical aliases; it cannot prove live account_id.",
        }


def _extract_lines(*, raw_text: str | None, lines: list[str] | None) -> list[str]:
    source_lines: list[str] = []
    if raw_text:
        source_lines.extend(raw_text.splitlines())
    if lines:
        source_lines.extend(lines)

    result: list[str] = []
    seen: set[str] = set()
    for value in source_lines:
        line = _clean_line(value)
        if not line:
            continue
        normalized = _normalize_name(line)
        if len(normalized) < 2 or normalized in seen or _looks_like_ui_noise(normalized):
            continue
        seen.add(normalized)
        result.append(line)
    return result[:80]


def _matches_for_line(line: str, candidates: list[dict], *, threshold: float) -> list[dict[str, Any]]:
    per_player: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for alias in _aliases_for_candidate(candidate):
            confidence, method = _score(line, alias)
            if confidence < threshold:
                continue
            player_key = _canonical_dota_player_id(candidate.get("player_id"))
            current = per_player.get(player_key)
            if current is None or (confidence, int(candidate.get("encounter_count") or 0)) > (
                current["confidence"],
                int(current["player"].get("encounter_count") or 0),
            ):
                per_player[player_key] = {
                    "player": candidate,
                    "matched_name": alias,
                    "confidence": round(confidence, 3),
                    "method": method,
                }

    strong_methods = {"exact_name", "alias_in_ocr_line"}
    strong_matches = [
        item for item in per_player.values() if item["method"] in strong_methods
    ]
    if strong_matches:
        return sorted(
            strong_matches,
            key=lambda item: (item["confidence"], int(item["player"].get("encounter_count") or 0)),
            reverse=True,
        )[:10]

    if not per_player:
        return []
    best = max(
        per_player.values(),
        key=lambda item: (item["confidence"], int(item["player"].get("encounter_count") or 0)),
    )
    return [best]


def _aliases_for_candidate(candidate: dict) -> list[str]:
    aliases: list[str] = []
    latest_name = candidate.get("latest_name")
    if latest_name:
        aliases.append(str(latest_name))
    encounter_names = candidate.get("encounter_names")
    if encounter_names:
        aliases.extend(str(name) for name in str(encounter_names).split(",") if name)

    result: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        cleaned = _clean_line(alias)
        normalized = _normalize_name(cleaned)
        if cleaned and normalized and normalized not in seen:
            seen.add(normalized)
            result.append(cleaned)
    return result


def _score(ocr_line: str, alias: str) -> tuple[float, str]:
    normalized_line = _normalize_name(ocr_line)
    normalized_alias = _normalize_name(alias)
    if not normalized_line or not normalized_alias:
        return 0.0, "empty"
    if normalized_line == normalized_alias:
        return 1.0, "exact_name"
    if len(normalized_alias) >= 3 and normalized_alias in normalized_line:
        return 0.93, "alias_in_ocr_line"
    if len(normalized_line) >= 3 and normalized_line in normalized_alias:
        return 0.88, "ocr_line_in_alias"
    return SequenceMatcher(None, normalized_line, normalized_alias).ratio(), "fuzzy_name"


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip())


def _normalize_name(value: str) -> str:
    lowered = value.casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)


def _looks_like_ui_noise(normalized: str) -> bool:
    noise = {
        "kills",
        "deaths",
        "assists",
        "networth",
        "level",
        "scoreboard",
        "radiant",
        "dire",
        "dota2",
        "victory",
        "defeat",
        "watch",
        "shop",
        "settings",
    }
    return normalized in noise


def _same_dota_player_id(left: Any, right: Any) -> bool:
    left_ids = _equivalent_dota_ids(left)
    right_ids = _equivalent_dota_ids(right)
    return bool(left_ids and right_ids and left_ids.intersection(right_ids))


def _equivalent_dota_ids(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    text = str(value)
    ids = {text}
    try:
        numeric = int(text)
    except ValueError:
        return ids
    steam64_base = 76561197960265728
    if numeric > steam64_base:
        ids.add(str(numeric - steam64_base))
    else:
        ids.add(str(numeric + steam64_base))
    return ids


def _canonical_dota_player_id(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    try:
        numeric = int(text)
    except ValueError:
        return text
    steam64_base = 76561197960265728
    if numeric > steam64_base:
        return str(numeric - steam64_base)
    return text
