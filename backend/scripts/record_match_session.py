from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value).strip("-")


def read_json_url(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def post_json_url(base_url: str, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, item: dict[str, Any]) -> None:
    with LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_events_since(db_path: Path, last_created_at: str | None, limit: int = 200) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if last_created_at:
            rows = conn.execute(
                """
                SELECT id, event_type, payload_json, created_at
                FROM events
                WHERE created_at > ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (last_created_at, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, event_type, payload_json, created_at
                FROM events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            rows = list(reversed(rows))
    items: list[dict[str, Any]] = []
    for row in rows:
        payload_raw = row["payload_json"]
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {"raw": payload_raw}
        items.append(
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"],
            }
        )
    return items


def load_recent_encounter_stats(db_path: Path, account_id: str) -> dict[str, Any]:
    if not db_path.exists():
        return {"error": f"db_not_found: {db_path}"}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        completed = conn.execute(
            """
            SELECT COUNT(DISTINCT match_id) AS matches, COUNT(*) AS encounters
            FROM encounters
            WHERE match_id IS NOT NULL
            """
        ).fetchone()
        pending_self = conn.execute(
            """
            SELECT COUNT(*) AS count, MAX(played_at) AS latest_played_at, MAX(updated_at) AS latest_updated_at
            FROM encounters
            WHERE match_id IS NULL AND player_steam_id = ?
            """,
            (account_id,),
        ).fetchone()
        recent_matches = conn.execute(
            """
            SELECT match_id, COUNT(*) AS rows, MIN(played_at) AS played_at, MAX(updated_at) AS updated_at
            FROM encounters
            WHERE match_id IS NOT NULL
            GROUP BY match_id
            ORDER BY CAST(match_id AS INTEGER) DESC
            LIMIT 8
            """
        ).fetchall()
    return {
        "completed": dict(completed) if completed else {},
        "pending_self": dict(pending_self) if pending_self else {},
        "recent_matches": [dict(row) for row in recent_matches],
    }


def compact_auto_ocr(status: dict[str, Any]) -> dict[str, Any]:
    result = status.get("match_result") or {}
    matches = result.get("matches") or []
    captures = status.get("captures") or {}
    slots = status.get("slot_texts") or []
    return {
        "enabled": status.get("enabled"),
        "state": status.get("state"),
        "phase": status.get("phase"),
        "progress_percent": status.get("progress_percent"),
        "progress_message": status.get("progress_message"),
        "capture_triggered_at": status.get("capture_triggered_at"),
        "capture_completed_at": status.get("capture_completed_at"),
        "ocr_started_at": status.get("ocr_started_at"),
        "ocr_completed_at": status.get("ocr_completed_at"),
        "ocr_current": status.get("ocr_current"),
        "ocr_total": status.get("ocr_total"),
        "duration_seconds": status.get("duration_seconds"),
        "raw_text": status.get("raw_text"),
        "match_count": len(matches),
        "matches": matches[:10],
        "captures": captures,
        "slot_texts": slots,
        "published_alerts": status.get("published_alerts"),
        "confirmation": status.get("confirmation"),
        "warning": status.get("warning"),
        "error": status.get("error"),
    }


def compact_alerts(alerts: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": alerts.get("headline"),
        "hit_count": alerts.get("hit_count"),
        "source": alerts.get("source"),
        "temp_match_key": alerts.get("temp_match_key"),
        "items": (alerts.get("items") or [])[:20],
        "updated_at": alerts.get("updated_at"),
    }


def make_snapshot(
    *,
    base_url: str,
    db_path: Path,
    account_id: str,
    timeout: float,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"timestamp": utc_now()}
    for name, path in {
        "health": "/health",
        "status": "/status",
        "auto_ocr": "/debug/ocr/auto",
        "gsi_id_probe": "/debug/gsi/id-probe",
        "alerts": "/alerts/current",
        "recent_encounters": "/encounters/recent?limit=20",
    }.items():
        try:
            value = read_json_url(base_url, path, timeout)
            if name == "auto_ocr":
                value = compact_auto_ocr(value)
            elif name == "alerts":
                value = compact_alerts(value)
            snapshot[name] = value
        except Exception as exc:
            snapshot[name] = {"error": str(exc)}
    snapshot["db_stats"] = load_recent_encounter_stats(db_path, account_id)
    return snapshot


def configure_auto_ocr(base_url: str, timeout: float, *, slots: bool, auto_focus: bool, debug_images: bool) -> list[dict[str, Any]]:
    actions = []
    for path, payload in [
        ("/debug/ocr/auto/slots", {"enabled": slots}),
        ("/debug/ocr/auto/focus", {"enabled": auto_focus}),
        ("/debug/ocr/auto/debug-images", {"enabled": debug_images}),
        ("/debug/ocr/auto/start", {}),
    ]:
        try:
            actions.append({"path": path, "ok": True, "response": post_json_url(base_url, path, payload, timeout)})
        except Exception as exc:
            actions.append({"path": path, "ok": False, "error": str(exc)})
    return actions


def run_backfill_check(
    *,
    timeline_path: Path,
    base_url: str,
    account_id: str,
    limit: int,
    timeout: float,
) -> None:
    append_jsonl(
        timeline_path,
        {
            "type": "backfill_check_started",
            "timestamp": utc_now(),
            "account_id": account_id,
            "limit": limit,
        },
    )
    try:
        preview = read_json_url(
            base_url,
            f"/backfill/recent?account_id={account_id}&limit={limit}",
            timeout,
        )
        resolved = post_json_url(
            base_url,
            "/backfill/resolve",
            {"account_id": account_id, "limit": limit},
            max(timeout, 60.0),
        )
        append_jsonl(
            timeline_path,
            {
                "type": "backfill_check",
                "timestamp": utc_now(),
                "preview": preview,
                "resolved": resolved,
            },
        )
    except Exception as exc:
        append_jsonl(
            timeline_path,
            {
                "type": "backfill_check_failed",
                "timestamp": utc_now(),
                "error": str(exc),
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a Dota2 Bounty full-flow debug session.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--account-id", default="126600075")
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duration-minutes", type=float, default=180.0)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--auto-backfill", action="store_true")
    parser.add_argument("--backfill-interval-seconds", type=float, default=180.0)
    parser.add_argument("--backfill-limit", type=int, default=10)
    parser.add_argument("--auto-ocr-slots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--auto-focus", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--debug-images", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--start-auto-ocr", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    runtime_dir: Path = args.runtime_dir
    session_id = args.session_id.strip() or datetime.now().strftime("match-session-%Y%m%d-%H%M%S")
    session_dir = runtime_dir / "match-sessions" / safe_name(session_id)
    timeline_path = session_dir / "timeline.jsonl"
    latest_path = session_dir / "latest.json"
    summary_path = session_dir / "summary.json"
    db_path = runtime_dir / "app.db"

    session_meta = {
        "session_id": session_id,
        "started_at": utc_now(),
        "base_url": args.base_url,
        "account_id": args.account_id,
        "interval": args.interval,
        "duration_minutes": args.duration_minutes,
        "auto_backfill": args.auto_backfill,
        "backfill_interval_seconds": args.backfill_interval_seconds,
        "backfill_limit": args.backfill_limit,
        "auto_ocr_slots": args.auto_ocr_slots,
        "auto_focus": args.auto_focus,
        "debug_images": args.debug_images,
        "paths": {
            "session_dir": str(session_dir.resolve()),
            "timeline": str(timeline_path.resolve()),
            "latest": str(latest_path.resolve()),
            "summary": str(summary_path.resolve()),
        },
    }
    write_json(summary_path, session_meta)
    append_jsonl(timeline_path, {"type": "session_started", **session_meta})

    if args.start_auto_ocr:
        actions = configure_auto_ocr(
            args.base_url,
            args.timeout,
            slots=args.auto_ocr_slots,
            auto_focus=args.auto_focus,
            debug_images=args.debug_images,
        )
        append_jsonl(timeline_path, {"type": "auto_ocr_configured", "timestamp": utc_now(), "actions": actions})

    start = time.monotonic()
    deadline = start + (args.duration_minutes * 60.0)
    last_event_created_at: str | None = None
    last_backfill_at = 0.0
    poll_count = 0
    last_snapshot: dict[str, Any] = {}
    backfill_thread: threading.Thread | None = None

    try:
        while time.monotonic() < deadline:
            poll_count += 1
            snapshot = make_snapshot(
                base_url=args.base_url,
                db_path=db_path,
                account_id=args.account_id,
                timeout=args.timeout,
            )
            last_snapshot = snapshot
            append_jsonl(timeline_path, {"type": "snapshot", "poll": poll_count, **snapshot})
            write_json(latest_path, {"poll": poll_count, **snapshot})

            events = load_events_since(db_path, last_event_created_at)
            if events:
                last_event_created_at = events[-1]["created_at"]
                append_jsonl(
                    timeline_path,
                    {
                        "type": "events",
                        "timestamp": utc_now(),
                        "count": len(events),
                        "items": events,
                    },
                )

            now = time.monotonic()
            if args.auto_backfill and now - last_backfill_at >= args.backfill_interval_seconds:
                if backfill_thread is None or not backfill_thread.is_alive():
                    last_backfill_at = now
                    backfill_thread = threading.Thread(
                        target=run_backfill_check,
                        kwargs={
                            "timeline_path": timeline_path,
                            "base_url": args.base_url,
                            "account_id": args.account_id,
                            "limit": args.backfill_limit,
                            "timeout": args.timeout,
                        },
                        name="match-session-backfill",
                        daemon=True,
                    )
                    backfill_thread.start()
            time.sleep(max(0.5, args.interval))
    except KeyboardInterrupt:
        append_jsonl(timeline_path, {"type": "session_interrupted", "timestamp": utc_now()})
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        append_jsonl(timeline_path, {"type": "session_error", "timestamp": utc_now(), "error": str(exc)})
        raise
    finally:
        if backfill_thread is not None and backfill_thread.is_alive():
            append_jsonl(
                timeline_path,
                {
                    "type": "backfill_check_still_running",
                    "timestamp": utc_now(),
                },
            )
        finished = {
            **session_meta,
            "finished_at": utc_now(),
            "poll_count": poll_count,
            "last_snapshot": last_snapshot,
        }
        write_json(summary_path, finished)
        append_jsonl(timeline_path, {"type": "session_finished", "timestamp": utc_now(), "poll_count": poll_count})

    print(json.dumps({"session_dir": str(session_dir.resolve()), "timeline": str(timeline_path.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
