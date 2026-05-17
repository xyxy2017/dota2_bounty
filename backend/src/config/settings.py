from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _default_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    base_dir: Path = field(default_factory=_default_base_dir)
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
    auto_ocr_enabled: bool = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_AUTO_OCR_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    )
    auto_ocr_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("DOTA2_BOUNTY_AUTO_OCR_INTERVAL_SECONDS", "6"))
    )
    auto_ocr_window_title: str = field(default_factory=lambda: os.getenv("DOTA2_BOUNTY_AUTO_OCR_WINDOW_TITLE", "Dota 2"))
    auto_ocr_process_name: str = field(default_factory=lambda: os.getenv("DOTA2_BOUNTY_AUTO_OCR_PROCESS_NAME", "dota2.exe"))
    auto_ocr_auto_focus: bool = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_AUTO_OCR_AUTO_FOCUS", "0").lower()
        in {"1", "true", "yes", "on"}
    )
    auto_ocr_use_slots: bool = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_AUTO_OCR_USE_SLOTS", "0").lower()
        in {"1", "true", "yes", "on"}
    )
    auto_ocr_debug_images: bool = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_AUTO_OCR_DEBUG_IMAGES", "0").lower()
        in {"1", "true", "yes", "on"}
    )
    auto_ocr_debug_max_runs: int = field(
        default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_AUTO_OCR_DEBUG_MAX_RUNS", "80"))
    )
    auto_ocr_tesseract_path: str = field(default_factory=lambda: os.getenv("DOTA2_BOUNTY_TESSERACT_PATH", "tesseract"))
    auto_ocr_tesseract_lang: str = field(default_factory=lambda: os.getenv("DOTA2_BOUNTY_TESSERACT_LANG", "eng+chi_sim"))
    auto_ocr_tesseract_psm: int = field(default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_TESSERACT_PSM", "7")))
    auto_ocr_tesseract_oem: int = field(default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_TESSERACT_OEM", "1")))
    auto_ocr_image_scale: int = field(default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_AUTO_OCR_IMAGE_SCALE", "4")))
    auto_ocr_image_threshold: int = field(default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_AUTO_OCR_IMAGE_THRESHOLD", "175")))
    auto_ocr_threshold: float = field(default_factory=lambda: float(os.getenv("DOTA2_BOUNTY_AUTO_OCR_THRESHOLD", "0.72")))
    auto_ocr_min_encounters: int = field(default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_AUTO_OCR_MIN_ENCOUNTERS", "2")))
    auto_ocr_confirm_scans: int = field(default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_AUTO_OCR_CONFIRM_SCANS", "2")))
    auto_ocr_cooldown_seconds: float = field(
        default_factory=lambda: float(os.getenv("DOTA2_BOUNTY_AUTO_OCR_COOLDOWN_SECONDS", "180"))
    )
    auto_ocr_require_tagged: bool = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_AUTO_OCR_REQUIRE_TAGGED", "0").lower()
        in {"1", "true", "yes", "on"}
    )
    gsi_id_probe_enabled: bool = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_GSI_ID_PROBE_ENABLED", "1").lower()
        in {"1", "true", "yes", "on"}
    )
    gsi_id_probe_write_raw_payloads: bool = field(
        default_factory=lambda: os.getenv("DOTA2_BOUNTY_GSI_ID_PROBE_WRITE_RAW", "1").lower()
        in {"1", "true", "yes", "on"}
    )
    gsi_id_probe_max_payloads: int = field(
        default_factory=lambda: int(os.getenv("DOTA2_BOUNTY_GSI_ID_PROBE_MAX_PAYLOADS", "200"))
    )

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
    auto_ocr_dir: Path = field(init=False)
    gsi_id_probe_dir: Path = field(init=False)

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
        self.auto_ocr_dir = Path(os.getenv("DOTA2_BOUNTY_AUTO_OCR_DIR", runtime_dir / "auto-ocr"))
        self.auto_ocr_dir.mkdir(parents=True, exist_ok=True)
        self.gsi_id_probe_dir = Path(os.getenv("DOTA2_BOUNTY_GSI_ID_PROBE_DIR", runtime_dir / "gsi-id-probe"))
        self.gsi_id_probe_dir.mkdir(parents=True, exist_ok=True)
