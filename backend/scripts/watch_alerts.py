from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config.settings import Settings
from services.alert_notify_service import AlertNotifyService


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch current alert feed and send local notifications")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run one notification check and exit")
    args = parser.parse_args()

    settings = Settings()
    service = AlertNotifyService(
        alerts_path=settings.alerts_path,
        state_path=settings.notifier_state_path,
        alerts_log_path=settings.alerts_log_path,
        platform_name=platform.system(),
    )

    if args.once:
        print(json.dumps(service.run_once(), ensure_ascii=False, indent=2))
        return

    print(
        json.dumps(
            {
                "mode": "watch",
                "alerts_path": str(settings.alerts_path),
                "state_path": str(settings.notifier_state_path),
                "alerts_log_path": str(settings.alerts_log_path),
                "interval": args.interval,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    service.watch_forever(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
