from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RuntimeStatusWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, status: dict[str, Any]) -> None:
        payload = dict(status)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def merge(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.read()
        current.update(patch)
        self.write(current)
        return self.read()

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))
