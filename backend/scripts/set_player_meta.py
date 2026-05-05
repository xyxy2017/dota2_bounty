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
from storage.db import Database
from storage.repositories.players_repo import PlayersRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Set player tag/note by player id")
    parser.add_argument("player_id", help="Steam ID or normalized player ID")
    parser.add_argument("--tag", default=None, help="Tag value, e.g. enemy/teammate")
    parser.add_argument("--note", default=None, help="Free text note")
    args = parser.parse_args()

    settings = Settings()
    repo = PlayersRepository(Database(settings.database_path))
    player = repo.update_player_meta(
        player_id=args.player_id,
        tag=args.tag,
        note=args.note,
    )
    if not player:
        raise SystemExit(f"player not found: {args.player_id}")
    print(json.dumps(player, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
