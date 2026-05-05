from __future__ import annotations

from typing import Any

from domain.models import PlayerMetaUpdate
from storage.repositories.players_repo import PlayersRepository


class OperatorService:
    def __init__(self, players_repo: PlayersRepository) -> None:
        self.players_repo = players_repo

    def update_player_meta(self, update: PlayerMetaUpdate) -> dict[str, Any] | None:
        return self.players_repo.update_player_meta(
            player_id=update.player_id,
            tag=update.tag,
            note=update.note,
        )

    def get_player(self, player_id: str) -> dict[str, Any] | None:
        return self.players_repo.get_by_id(player_id)

    def list_recent_players(self, limit: int, exclude_player_id: str | None = None) -> list[dict[str, Any]]:
        return self.players_repo.list_recent_players(limit=limit, exclude_player_id=exclude_player_id)

    def list_tag_presets(self) -> list[dict[str, Any]]:
        return [
            {"tag": "仇人", "priority": 100, "description": "优先提醒，默认按高风险处理"},
            {"tag": "毒瘤", "priority": 95, "description": "优先提醒，偏负面玩家"},
            {"tag": "避雷", "priority": 90, "description": "优先提醒，建议重点留意"},
            {"tag": "关注", "priority": 70, "description": "普通高亮，后续再判断"},
            {"tag": "大腿", "priority": 65, "description": "积极标签，偏可靠队友"},
            {"tag": "靠谱队友", "priority": 60, "description": "优先展示的正面队友"},
            {"tag": "绝活哥", "priority": 55, "description": "英雄池或绝活特征明显"},
        ]
