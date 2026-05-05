from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import Settings
from storage.db import Database


def main() -> None:
    settings = Settings()
    Database(settings.database_path).initialize()
    print(f"initialized: {settings.database_path}")


if __name__ == "__main__":
    main()
