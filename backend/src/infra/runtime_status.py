from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RuntimeStatusWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def write(self, status: dict[str, Any]) -> None:
        with self._lock:
            payload = dict(status)
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, self.path)

    def merge(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.read()
            current.update(patch)
            self.write(current)
            return self.read()

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                raw = self.path.read_text(encoding="utf-8")
                return json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                corrupt_path = self.path.with_suffix(f"{self.path.suffix}.corrupt")
                try:
                    corrupt_path.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass
                return {}
