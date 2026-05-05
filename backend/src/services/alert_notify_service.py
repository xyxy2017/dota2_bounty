from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AlertNotifyService:
    def __init__(
        self,
        *,
        alerts_path: Path,
        state_path: Path,
        alerts_log_path: Path,
        platform_name: str,
    ) -> None:
        self.alerts_path = alerts_path
        self.state_path = state_path
        self.alerts_log_path = alerts_log_path
        self.platform_name = platform_name

    def run_once(self) -> dict[str, Any]:
        alerts = self._read_json(self.alerts_path)
        state = self._read_json(self.state_path)
        signature = self._build_signature(alerts)
        last_signature = state.get("last_signature")

        result = {
            "delivered": False,
            "reason": "no_alerts",
            "signature": signature,
            "headline": alerts.get("headline"),
            "hit_count": int(alerts.get("hit_count") or 0),
        }

        if not alerts.get("items"):
            self._write_state(signature=signature, headline=None, delivered_at=None)
            result["reason"] = "empty_items"
            return result

        if signature == last_signature:
            result["reason"] = "duplicate"
            return result

        body = self._build_notification_body(alerts)
        notify_ok = self._dispatch_notification(
            title=alerts.get("headline") or "Dota2 Bounty Alert",
            body=body,
        )
        delivered_at = datetime.now(timezone.utc).isoformat()
        self._append_alert_log(alerts=alerts, delivered_at=delivered_at, notify_ok=notify_ok)
        self._write_state(
            signature=signature,
            headline=alerts.get("headline"),
            delivered_at=delivered_at,
        )
        result["delivered"] = True
        result["reason"] = "sent"
        result["body"] = body
        result["notify_ok"] = notify_ok
        return result

    def watch_forever(self, interval_seconds: float) -> None:
        safe_interval = max(0.5, float(interval_seconds))
        while True:
            self.run_once()
            time.sleep(safe_interval)

    def _build_signature(self, alerts: dict[str, Any]) -> str:
        raw = json.dumps(
            {
                "headline": alerts.get("headline"),
                "items": alerts.get("items", []),
                "temp_match_key": alerts.get("temp_match_key"),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _build_notification_body(self, alerts: dict[str, Any]) -> str:
        items = alerts.get("items") or []
        lines: list[str] = []
        for item in items[:3]:
            player_name = item.get("player_name") or item.get("player_id") or "unknown"
            summary = item.get("summary_text") or ""
            lines.append(f"{player_name}: {summary}".strip())
        return "\n".join(lines)

    def _dispatch_notification(self, *, title: str, body: str) -> bool:
        if self.platform_name == "Darwin":
            script = (
                'display notification "{}" with title "{}"'
            ).format(_escape_applescript(body), _escape_applescript(title))
            try:
                subprocess.run(
                    ["osascript", "-e", script],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                return False
        return False

    def _append_alert_log(self, *, alerts: dict[str, Any], delivered_at: str, notify_ok: bool) -> None:
        self.alerts_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "delivered_at": delivered_at,
            "notify_ok": notify_ok,
            "headline": alerts.get("headline"),
            "hit_count": alerts.get("hit_count"),
            "items": alerts.get("items", []),
        }
        with self.alerts_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_state(self, *, signature: str, headline: str | None, delivered_at: str | None) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "last_signature": signature,
                    "last_headline": headline,
                    "last_delivered_at": delivered_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
