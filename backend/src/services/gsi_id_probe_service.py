from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLAYER_ID_KEYS = {
    "account_id",
    "accountid",
    "accountId",
    "steam_id",
    "steamid",
    "steamId",
    "steam_id64",
    "steamid64",
    "xuid",
}

PLAYER_CONTEXT_KEYS = {
    "name",
    "team",
    "team_name",
    "teamName",
    "hero",
    "hero_id",
    "heroId",
    "hero_name",
    "heroName",
    "player_slot",
    "playerSlot",
    "is_local_player",
    "isLocalPlayer",
}

ROSTER_HINTS = {"allplayers", "players", "roster", "team2", "team3", "radiant", "dire"}
LOCAL_HINTS = {"player", "provider"}
STEAM64_BASE = 76561197960265728


@dataclass(frozen=True)
class ProbeConfig:
    output_dir: Path
    default_account_id: str | None = None
    enabled: bool = True
    write_raw_payloads: bool = True
    max_payloads: int = 200


class GsiIdProbeService:
    def __init__(self, config: ProbeConfig) -> None:
        self.output_dir = config.output_dir
        self.default_account_id = str(config.default_account_id) if config.default_account_id else None
        self.enabled = config.enabled
        self.write_raw_payloads = config.write_raw_payloads
        self.max_payloads = max(1, min(2000, int(config.max_payloads)))
        self.status_path = self.output_dir / "status.json"
        self.raw_dir = self.output_dir / "raw"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._payload_count = 0
        self._last_status: dict[str, Any] = self._load_status()

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            status = self.status()
            status["enabled"] = False
            self._write_status(status)
            return status

        self._payload_count += 1
        observed_at = datetime.now(timezone.utc).isoformat()
        raw_payload_path = self._write_raw_payload(payload, observed_at) if self.write_raw_payloads else None
        all_candidates = _collect_id_candidates(payload)
        player_objects = _collect_player_objects(payload)
        top_level_keys = sorted(str(key) for key in payload.keys())
        phase = _extract_phase(payload)
        match_id = _extract_match_id(payload)
        local_ids = _extract_local_ids(payload)
        normalized_local_ids = {
            normalized
            for item in local_ids
            for normalized in [_normalize_player_id(item.get("value"))]
            if normalized
        }
        if self.default_account_id:
            normalized_local_ids.add(self.default_account_id)

        normalized_player_objects = [_normalize_player_object(item) for item in player_objects]
        unique_ids = sorted(
            {
                item["normalized_id"]
                for item in normalized_player_objects
                if item.get("normalized_id")
            }
        )
        non_local_ids = [item for item in unique_ids if item not in normalized_local_ids]
        roster_objects = [
            item
            for item in normalized_player_objects
            if item.get("normalized_id") and item.get("scope") == "roster"
        ]
        local_objects = [
            item
            for item in normalized_player_objects
            if item.get("normalized_id") and item.get("scope") == "local"
        ]
        conclusion = _build_conclusion(
            unique_ids=unique_ids,
            non_local_ids=non_local_ids,
            roster_objects=roster_objects,
            local_objects=local_objects,
            phase=phase,
        )
        status = {
            "enabled": True,
            "observed_payload_count": int(self._last_status.get("observed_payload_count") or 0) + 1,
            "process_payload_count": self._payload_count,
            "observed_at": observed_at,
            "phase": phase,
            "match_id": match_id,
            "top_level_keys": top_level_keys,
            "raw_payload_path": str(raw_payload_path) if raw_payload_path else None,
            "raw_payload_dir": str(self.raw_dir),
            "candidate_id_count": len(all_candidates),
            "candidate_ids": all_candidates[:80],
            "player_object_count": len(normalized_player_objects),
            "player_objects": normalized_player_objects[:40],
            "unique_player_ids": unique_ids,
            "unique_player_id_count": len(unique_ids),
            "local_player_ids": sorted(normalized_local_ids),
            "non_local_player_ids": non_local_ids,
            "non_local_player_id_count": len(non_local_ids),
            "roster_player_id_count": len({item["normalized_id"] for item in roster_objects if item.get("normalized_id")}),
            "local_player_object_count": len(local_objects),
            "has_direct_other_player_ids": bool(non_local_ids),
            "has_roster_like_payload": bool(roster_objects),
            "conclusion": conclusion,
        }
        self._write_status(status)
        self._cleanup_raw_payloads()
        return status

    def status(self) -> dict[str, Any]:
        self._last_status = self._load_status()
        if not self._last_status:
            return {
                "enabled": self.enabled,
                "observed_payload_count": 0,
                "raw_payload_dir": str(self.raw_dir),
                "has_direct_other_player_ids": False,
                "conclusion": "尚未收到 GSI payload。",
            }
        status = dict(self._last_status)
        status["enabled"] = self.enabled
        status["raw_payload_dir"] = str(self.raw_dir)
        return status

    def reset(self) -> dict[str, Any]:
        if self.raw_dir.exists():
            for child in self.raw_dir.glob("*.json"):
                child.unlink(missing_ok=True)
        self._payload_count = 0
        status = {
            "enabled": self.enabled,
            "observed_payload_count": 0,
            "raw_payload_dir": str(self.raw_dir),
            "has_direct_other_player_ids": False,
            "conclusion": "探针已重置，等待新的 GSI payload。",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_status(status)
        return status

    def list_raw_payloads(self, limit: int = 50) -> dict[str, Any]:
        safe_limit = max(1, min(500, int(limit)))
        items = []
        if self.raw_dir.exists():
            for path in sorted(self.raw_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:safe_limit]:
                items.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "size": path.stat().st_size,
                        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                    }
                )
        return {"items": items, "raw_payload_dir": str(self.raw_dir), "limit": safe_limit}

    def _write_raw_payload(self, payload: dict[str, Any], observed_at: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.raw_dir / f"{timestamp}.json"
        envelope = {
            "observed_at": observed_at,
            "payload": payload,
        }
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _cleanup_raw_payloads(self) -> None:
        if not self.raw_dir.exists():
            return
        paths = sorted(self.raw_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in paths[self.max_payloads :]:
            stale.unlink(missing_ok=True)

    def _load_status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {}
        try:
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = self.status_path.with_suffix(".corrupt.json")
            try:
                shutil.copy2(self.status_path, backup)
            except OSError:
                pass
            return {}

    def _write_status(self, status: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(status)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._last_status = payload


def _collect_id_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child_path = f"{path}.{key}" if path else str(key)
                if key in PLAYER_ID_KEYS:
                    normalized = _normalize_player_id(value)
                    items.append(
                        {
                            "path": child_path,
                            "key": key,
                            "value": str(value) if value is not None else None,
                            "normalized_id": normalized,
                            "scope": _classify_scope(child_path),
                        }
                    )
                walk(value, child_path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload, "")
    return items


def _collect_player_objects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            keys = set(node.keys())
            if keys & PLAYER_ID_KEYS and (keys & PLAYER_CONTEXT_KEYS or _classify_scope(path) == "roster"):
                compact: dict[str, Any] = {
                    "path": path,
                    "scope": _classify_scope(path),
                    "account_id": _first_present(node, ["account_id", "accountid", "accountId"]),
                    "steam_id": _first_present(node, ["steam_id", "steamid", "steamId", "steam_id64", "steamid64", "xuid"]),
                    "name": _first_present(node, ["name", "player_name", "playerName"]),
                    "team": _first_present(node, ["team", "team_name", "teamName"]),
                    "hero_id": _first_present(node, ["hero_id", "heroId"]),
                    "hero_name": _first_present(node, ["hero_name", "heroName"]),
                    "player_slot": _first_present(node, ["player_slot", "playerSlot"]),
                }
                hero = node.get("hero")
                if isinstance(hero, dict):
                    compact["hero_id"] = compact.get("hero_id") or _first_present(hero, ["id", "hero_id", "heroId"])
                    compact["hero_name"] = compact.get("hero_name") or _first_present(hero, ["name", "localized_name"])
                elif isinstance(hero, str):
                    compact["hero_name"] = compact.get("hero_name") or hero
                items.append(compact)
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload, "")
    return items


def _normalize_player_object(item: dict[str, Any]) -> dict[str, Any]:
    normalized_id = _normalize_player_id(item.get("account_id") or item.get("steam_id"))
    output = {key: value for key, value in item.items() if value not in (None, "")}
    output["normalized_id"] = normalized_id
    return output


def _first_present(node: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in node and node[key] not in (None, ""):
            return node[key]
    return None


def _normalize_player_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = int(text)
    except ValueError:
        return text
    if numeric > STEAM64_BASE:
        return str(numeric - STEAM64_BASE)
    return str(numeric)


def _classify_scope(path: str) -> str:
    lowered = path.lower()
    if any(hint in lowered for hint in ROSTER_HINTS):
        return "roster"
    parts = {part.strip("[]0123456789").lower() for part in lowered.replace("[", ".").replace("]", "").split(".")}
    if parts & LOCAL_HINTS:
        return "local"
    return "unknown"


def _extract_phase(payload: dict[str, Any]) -> str | None:
    map_data = payload.get("map") if isinstance(payload.get("map"), dict) else {}
    phase = (
        map_data.get("game_state")
        or map_data.get("phase")
        or map_data.get("name")
        or payload.get("game_state")
        or payload.get("phase")
    )
    return str(phase) if phase not in (None, "") else None


def _extract_match_id(payload: dict[str, Any]) -> str | None:
    map_data = payload.get("map") if isinstance(payload.get("map"), dict) else {}
    match_id = map_data.get("matchid") or map_data.get("match_id") or payload.get("match_id") or payload.get("matchid")
    return str(match_id) if match_id not in (None, "") else None


def _extract_local_ids(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for section_name in ("provider", "player"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in PLAYER_ID_KEYS:
            if key in section:
                items.append({"path": f"{section_name}.{key}", "value": section[key]})
    return items


def _build_conclusion(
    *,
    unique_ids: list[str],
    non_local_ids: list[str],
    roster_objects: list[dict[str, Any]],
    local_objects: list[dict[str, Any]],
    phase: str | None,
) -> str:
    if non_local_ids:
        return f"GSI payload 中发现 {len(non_local_ids)} 个非本地玩家 ID，可尝试直接 ID 路径。"
    if roster_objects:
        return "GSI payload 出现 roster-like 结构，但当前只识别到本地 ID 或空 ID。"
    if local_objects or unique_ids:
        return "GSI payload 当前只识别到本地玩家 ID，仍需要 OCR 识别其他玩家昵称。"
    if phase:
        return f"GSI payload 已到达阶段 {phase}，但没有发现玩家 ID 字段。"
    return "GSI payload 中没有发现可用玩家 ID 字段。"
