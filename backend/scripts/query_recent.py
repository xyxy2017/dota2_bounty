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
from storage.repositories.encounters_repo import EncountersRepository
from storage.repositories.events_repo import EventsRepository
from storage.repositories.players_repo import PlayersRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Query recent MVP data")
    parser.add_argument("--events", type=int, default=10, help="Number of recent events")
    parser.add_argument("--encounters", type=int, default=10, help="Number of recent encounters")
    parser.add_argument("--players", type=int, default=10, help="Number of recent players")
    parser.add_argument("--repeats", type=int, default=10, help="Number of repeated players")
    parser.add_argument(
        "--min-encounters",
        type=int,
        default=2,
        help="Minimum number of encounters for repeated player aggregation",
    )
    parser.add_argument(
        "--exclude-player-id",
        default=None,
        help="Exclude a player id from repeated player aggregation",
    )
    args = parser.parse_args()

    settings = Settings()
    db = Database(settings.database_path)

    players = PlayersRepository(db).list_recent_players(limit=args.players)
    encounters_repo = EncountersRepository(db)
    encounters = encounters_repo.list_recent_encounters(limit=args.encounters)
    events = EventsRepository(db).list_recent_events(limit=args.events)
    repeats = [
        {
            "player_id": item.player_id,
            "latest_name": item.latest_name,
            "encounter_count": item.encounter_count,
            "teammate_count": item.teammate_count,
            "opponent_count": item.opponent_count,
            "win_count": item.win_count,
            "lose_count": item.lose_count,
            "last_encounter_at": item.last_encounter_at,
            "match_ids": item.match_ids,
        }
        for item in encounters_repo.list_repeated_players(
            limit=args.repeats,
            min_encounters=args.min_encounters,
            exclude_player_id=args.exclude_player_id,
        )
    ]

    print(
        json.dumps(
            {
                "players": players,
                "encounters": encounters,
                "events": events,
                "repeated_players": repeats,
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
