from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    server_host: str = field(default_factory=lambda: os.getenv("DOTA2_BOUNTY_SERVER_HOST", "127.0.0.1"))
    server_port: int = field(default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_SERVER_PORT", "8000")))
    log_level: str = field(default_factory=lambda: os.getenv("DOTA2_BOUNTY_LOG_LEVEL", "INFO"))
    opendota_api_base: str = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_OPENDOTA_API_BASE", "https://api.opendota.com/api")
    )
    opendota_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("DOTA2_BOUNTY_OPENDOTA_TIMEOUT_SECONDS", "12"))
    )
    opendota_backfill_default_limit: int = field(
        default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_OPENDOTA_BACKFILL_DEFAULT_LIMIT", "20"))
    )
    default_account_id: str | None = field(default_factory=lambda: os.getenv("DOTA2_BOUNTY_ACCOUNT_ID", "126600075"))

    runtime_dir: Path = field(init=False)
    ui_dir: Path = field(init=False)
    database_path: Path = field(init=False)
    runtime_status_path: Path = field(init=False)
    alerts_path: Path = field(init=False)
    notifier_state_path: Path = field(init=False)
    alerts_log_path: Path = field(init=False)
    hero_catalog_path: Path = field(init=False)
    log_file_path: Path = field(init=False)
    lock_file_path: Path = field(init=False)

    def __post_init__(self) -> None:
        runtime_dir = Path(os.getenv("DOTA2_BOUNTY_RUNTIME_DIR", self.base_dir / "runtime"))
        logs_dir = runtime_dir / "logs"
        ui_dir = Path(os.getenv("DOTA2_BOUNTY_UI_DIR", self.base_dir / "src" / "web"))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        self.runtime_dir = runtime_dir
        self.ui_dir = ui_dir
        self.database_path = Path(os.getenv("DOTA2_BOUNTY_DATABASE_PATH", runtime_dir / "app.db"))
        self.runtime_status_path = Path(
            os.getenv("DOTA2_BOUNTY_RUNTIME_STATUS_PATH", runtime_dir / "runtime-status.json")
        )
        self.alerts_path = Path(os.getenv("DOTA2_BOUNTY_ALERTS_PATH", runtime_dir / "alerts.json"))
        self.notifier_state_path = Path(
            os.getenv("DOTA2_BOUNTY_NOTIFIER_STATE_PATH", runtime_dir / "notifier-state.json")
        )
        self.alerts_log_path = Path(
            os.getenv("DOTA2_BOUNTY_ALERTS_LOG_PATH", logs_dir / "alerts.log")
        )
        self.hero_catalog_path = Path(
            os.getenv("DOTA2_BOUNTY_HERO_CATALOG_PATH", runtime_dir / "hero-catalog.json")
        )
        self.log_file_path = Path(os.getenv("DOTA2_BOUNTY_LOG_FILE_PATH", logs_dir / "backend.log"))
        self.lock_file_path = Path(os.getenv("DOTA2_BOUNTY_LOCK_FILE_PATH", runtime_dir / "backend.lock"))
