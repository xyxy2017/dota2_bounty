from __future__ import annotations

import shutil
import subprocess
import threading
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infra.runtime_status import RuntimeStatusWriter

BACKEND_DIR = Path(__file__).resolve().parents[2]
TOP_BAR_LEFT_X = 10
TOP_BAR_RIGHT_X = 57
TOP_BAR_Y = 8
TOP_BAR_W = 33
TOP_BAR_H = 2.4


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _powershell_executable() -> str:
    # Use shell resolution first for maximum compatibility across host bitness.
    # In some environments, forcing Sysnative regressed capture stability.
    return "powershell.exe"


class AutoOcrService:
    def __init__(
        self,
        *,
        output_dir: Path,
        runtime_status: RuntimeStatusWriter,
        ocr_match_service,
        alerts_writer: RuntimeStatusWriter,
        events_repo,
        default_account_id: str | None,
        tesseract_path: str,
        tesseract_lang: str,
        tesseract_psm: int,
        tesseract_oem: int,
        image_scale: int,
        image_threshold: int,
        window_title: str,
        process_name: str,
        auto_focus: bool,
        use_slots: bool,
        debug_images: bool,
        debug_max_runs: int,
        interval_seconds: float,
        threshold: float,
        min_encounters: int,
        confirm_scans: int,
        cooldown_seconds: float,
        require_tagged: bool,
        alert_payload_builder,
    ) -> None:
        self.output_dir = output_dir
        self.runtime_status = runtime_status
        self.ocr_match_service = ocr_match_service
        self.alerts_writer = alerts_writer
        self.events_repo = events_repo
        self.default_account_id = default_account_id
        self.configured_tesseract_path = tesseract_path
        tesseract_resolution = _resolve_tesseract_path(tesseract_path)
        self.tesseract_path = tesseract_resolution["path"]
        self.tesseract_source = tesseract_resolution["source"]
        self.tesseract_checked_paths = tesseract_resolution["checked_paths"]
        self.requested_tesseract_lang = tesseract_lang or "eng+chi_sim"
        lang_resolution = _resolve_tesseract_lang(self.tesseract_path, self.requested_tesseract_lang)
        self.tesseract_lang = lang_resolution["lang"]
        self.tesseract_missing_langs = lang_resolution["missing_langs"]
        self.tesseract_tessdata_dir = lang_resolution["tessdata_dir"]
        self.tesseract_psm = _bounded_int(tesseract_psm, default=7, minimum=0, maximum=13)
        self.tesseract_oem = _bounded_int(tesseract_oem, default=1, minimum=0, maximum=3)
        self.image_scale = _bounded_int(image_scale, default=4, minimum=1, maximum=8)
        self.image_threshold = _bounded_int(image_threshold, default=175, minimum=0, maximum=255)
        self.window_title = window_title
        self.process_name = process_name or "dota2.exe"
        self.auto_focus = auto_focus
        self.use_slots = bool(use_slots)
        self.debug_images = debug_images
        self.debug_max_runs = _bounded_int(debug_max_runs, default=80, minimum=1, maximum=2000)
        self.debug_dir = self.output_dir / "debug"
        self.interval_seconds = max(3.0, float(interval_seconds or 6))
        self.threshold = threshold
        self.min_encounters = min_encounters
        self.confirm_scans = _bounded_int(confirm_scans, default=2, minimum=1, maximum=10)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds or 0))
        self.require_tagged = require_tagged
        self.alert_payload_builder = alert_payload_builder
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_status: dict[str, Any] = {}
        self._last_capture_status: dict[str, Any] = {}
        self._stable_hit_key: str | None = None
        self._stable_hit_count = 0
        self._last_published_hits: dict[str, datetime] = {}

    def start(self) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return self.status()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="auto-ocr-worker", daemon=True)
        self._thread.start()
        self._write_status({"enabled": True, "state": "running"})
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        # Preserve the latest capture/OCR payload for pause-and-review in UI.
        preserved = dict(self._last_capture_status or self.status())
        self._write_status(
            {
                **preserved,
                "enabled": False,
                "state": "stopped",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return self.status()

    def set_auto_focus(self, enabled: bool) -> dict[str, Any]:
        self.auto_focus = bool(enabled)
        self._write_status(
            {
                **self.status(),
                "auto_focus": self.auto_focus,
                "focus_paused": not self.auto_focus,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return self.status()

    def set_debug_images(self, enabled: bool) -> dict[str, Any]:
        self.debug_images = bool(enabled)
        self._write_status(
            {
                **self.status(),
                "debug_images": self.debug_images,
                "debug_dir": str(self.debug_dir),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return self.status()

    def set_use_slots(self, enabled: bool) -> dict[str, Any]:
        self.use_slots = bool(enabled)
        self._write_status(
            {
                **self.status(),
                "use_slots": self.use_slots,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return self.status()

    def status(self) -> dict[str, Any]:
        state = dict(self._last_status)
        state.setdefault("enabled", bool(self._thread and self._thread.is_alive()))
        state.setdefault("interval_seconds", self.interval_seconds)
        state.setdefault("window_title", self.window_title)
        state.setdefault("process_name", self.process_name)
        state.setdefault("auto_focus", self.auto_focus)
        state.setdefault("use_slots", self.use_slots)
        state.setdefault("debug_images", self.debug_images)
        state.setdefault("debug_dir", str(self.debug_dir))
        state.setdefault("debug_max_runs", self.debug_max_runs)
        state.setdefault("configured_tesseract_path", self.configured_tesseract_path)
        state.setdefault("tesseract_path", self.tesseract_path)
        state.setdefault("tesseract_source", self.tesseract_source)
        state.setdefault("tesseract_checked_paths", self.tesseract_checked_paths)
        state.setdefault("requested_tesseract_lang", self.requested_tesseract_lang)
        state.setdefault("tesseract_lang", self.tesseract_lang)
        state.setdefault("tesseract_missing_langs", self.tesseract_missing_langs)
        state.setdefault("tesseract_tessdata_dir", self.tesseract_tessdata_dir)
        state.setdefault("tesseract_psm", self.tesseract_psm)
        state.setdefault("tesseract_oem", self.tesseract_oem)
        state.setdefault("image_scale", self.image_scale)
        state.setdefault("image_threshold", self.image_threshold)
        state.setdefault("confirm_scans", self.confirm_scans)
        state.setdefault("cooldown_seconds", self.cooldown_seconds)
        state.setdefault("output_dir", str(self.output_dir))
        state.setdefault("needs_tesseract", not self._tesseract_available())
        return state

    def run_once(self, *, publish_alerts: bool = True, capture_only: bool = False) -> dict[str, Any]:
        with self._lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            run_started_at = datetime.now(timezone.utc)
            self._write_progress(
                state="capture_pending",
                phase="capturing",
                progress_percent=5,
                run_started_at=run_started_at,
                capture_triggered_at=run_started_at,
                message="正在准备截图",
            )
            try:
                captures = self._capture_regions_capture_only() if capture_only else self._capture_regions()
            except Exception as exc:
                error_text = str(exc)
                error_details = _capture_error_details(error_text)
                preserved = dict(self._last_capture_status)
                paused_for_foreground = error_details["state"] == "dota_not_foreground"
                status = {
                    **preserved,
                    "enabled": bool(self._thread and self._thread.is_alive()),
                    "state": error_details["state"],
                    "phase": "paused" if paused_for_foreground else "capture_failed",
                    "progress_percent": 0 if paused_for_foreground else 100,
                    "progress_message": (
                        "Dota2 不在前台，已暂停截图并保留上一次结果"
                        if paused_for_foreground
                        else "截图失败"
                    ),
                    "error": error_text,
                    "window_title": self.window_title,
                    "process_name": self.process_name,
                    "auto_focus": self.auto_focus,
                    "debug_images": self.debug_images,
                    "debug_dir": str(self.debug_dir),
                    "debug_max_runs": self.debug_max_runs,
                    "focus_attempted": error_details["focus_attempted"],
                    "focus_succeeded": False,
                    "process_id": error_details.get("process_id"),
                    "foreground_process_id": error_details.get("foreground_process_id"),
                    "window_match_mode": error_details.get("window_match_mode"),
                    "capture_backend": "copy_from_screen_visible_pixels",
                    "dpi_aware": True,
                    "interval_seconds": self.interval_seconds,
                    "configured_tesseract_path": self.configured_tesseract_path,
                    "tesseract_path": self.tesseract_path,
                    "tesseract_source": self.tesseract_source,
                    "tesseract_checked_paths": self.tesseract_checked_paths,
                    "requested_tesseract_lang": self.requested_tesseract_lang,
                    "tesseract_lang": self.tesseract_lang,
                    "tesseract_missing_langs": self.tesseract_missing_langs,
                    "tesseract_tessdata_dir": self.tesseract_tessdata_dir,
                    "tesseract_psm": self.tesseract_psm,
                    "tesseract_oem": self.tesseract_oem,
                    "image_scale": self.image_scale,
                    "image_threshold": self.image_threshold,
                    "confirm_scans": self.confirm_scans,
                    "cooldown_seconds": self.cooldown_seconds,
                    "tesseract_available": self._tesseract_available(),
                    "preserved_last_capture": bool(preserved),
                    "run_started_at": run_started_at.isoformat(),
                    "capture_triggered_at": run_started_at.isoformat(),
                    "capture_completed_at": None,
                    "ocr_started_at": None,
                    "ocr_completed_at": None,
                    "duration_seconds": round((datetime.now(timezone.utc) - run_started_at).total_seconds(), 2),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._write_status(status)
                return status
            archive_dir = self._archive_test_images(captures=captures, mode="run-once")
            capture_completed_at = datetime.now(timezone.utc)
            self._write_progress(
                state="capture_completed",
                phase="ocr_pending" if not capture_only else "capture_completed",
                progress_percent=25 if not capture_only else 100,
                run_started_at=run_started_at,
                capture_triggered_at=run_started_at,
                capture_completed_at=capture_completed_at,
                message="截图完成" if capture_only else "截图完成，准备 OCR",
            )
            if capture_only:
                status = {
                    "enabled": bool(self._thread and self._thread.is_alive()),
                    "state": "captured_only" if captures else "no_capture",
                    "phase": "capture_completed",
                    "progress_percent": 100,
                    "progress_message": "截图完成",
                    "window_title": self.window_title,
                    "process_name": self.process_name,
                    "capture_only": True,
                    "captures": captures,
                    "archive_dir": str(archive_dir),
                    "run_started_at": run_started_at.isoformat(),
                    "capture_triggered_at": run_started_at.isoformat(),
                    "capture_completed_at": capture_completed_at.isoformat(),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._write_status(status)
                return status
            slot_texts: list[str] = []
            fallback_texts: list[str] = []
            tesseract_available = self._tesseract_available()
            if tesseract_available:
                to_ocr = []
                for capture in captures:
                    if self.use_slots and capture.get("kind") == "group":
                        capture["text"] = ""
                        continue
                    to_ocr.append(capture)

                # Parallel OCR for slot-heavy workloads (10 names) to reduce wall time.
                max_workers = max(1, min(6, len(to_ocr)))
                ocr_started_at = datetime.now(timezone.utc)
                self._write_progress(
                    state="ocr_running",
                    phase="ocr",
                    progress_percent=30,
                    run_started_at=run_started_at,
                    capture_triggered_at=run_started_at,
                    capture_completed_at=capture_completed_at,
                    ocr_started_at=ocr_started_at,
                    ocr_current=0,
                    ocr_total=len(to_ocr),
                    message=f"OCR 识别中 0/{len(to_ocr)}",
                )
                completed_ocr = 0
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_map = {
                        executor.submit(self._ocr_capture_text, capture): capture
                        for capture in to_ocr
                    }
                    for future in as_completed(future_map):
                        capture = future_map[future]
                        text = ""
                        try:
                            text = future.result()
                        except Exception:
                            text = ""
                        capture["text"] = text
                        completed_ocr += 1
                        self._write_progress(
                            state="ocr_running",
                            phase="ocr",
                            progress_percent=30 + int(50 * completed_ocr / max(1, len(to_ocr))),
                            run_started_at=run_started_at,
                            capture_triggered_at=run_started_at,
                            capture_completed_at=capture_completed_at,
                            ocr_started_at=ocr_started_at,
                            ocr_current=completed_ocr,
                            ocr_total=len(to_ocr),
                            message=f"OCR 识别中 {completed_ocr}/{len(to_ocr)}",
                        )

                if self.use_slots:
                    slot_captures = [c for c in captures if c.get("kind") == "slot"]
                    if _needs_slot_quality_fallback(slot_captures):
                        # Quality gate: if fast pass degrades into short garbage,
                        # re-run only weak slots with a stronger OCR profile.
                        weak_slots = [c for c in slot_captures if _is_weak_slot_text(c.get("text") or "")]
                        if weak_slots:
                            max_workers = max(1, min(4, len(weak_slots)))
                            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                                future_map = {
                                    executor.submit(self._ocr_capture_text_hq, capture): capture
                                    for capture in weak_slots
                                }
                                for future in as_completed(future_map):
                                    capture = future_map[future]
                                    try:
                                        capture["text"] = future.result() or capture.get("text", "")
                                    except Exception:
                                        pass
                            slot_texts = [c.get("text", "") for c in slot_captures if c.get("text")]
            else:
                for capture in captures:
                    capture["text"] = ""

            # Keep stable visual/topology order for slot text output and matching.
            ordered_slots = [
                c for c in captures if c.get("kind") == "slot"
            ]
            ordered_slots.sort(
                key=lambda c: (
                    0 if c.get("team") == "radiant" else 1,
                    int(c.get("slot_index") or 0),
                )
            )
            slot_texts = [str(c.get("text") or "").strip() for c in ordered_slots if str(c.get("text") or "").strip()]
            fallback_texts = [
                str(c.get("text") or "").strip()
                for c in captures
                if c.get("kind") != "slot" and str(c.get("text") or "").strip()
            ]

            if self.use_slots and slot_texts:
                repaired = self._repair_slot_lines_with_candidates(slot_texts)
                if repaired:
                    slot_texts = repaired

            match_started_at = datetime.now(timezone.utc)
            self._write_progress(
                state="matching",
                phase="matching",
                progress_percent=90,
                run_started_at=run_started_at,
                capture_triggered_at=run_started_at,
                capture_completed_at=capture_completed_at,
                ocr_started_at=locals().get("ocr_started_at"),
                ocr_completed_at=match_started_at,
                ocr_current=locals().get("completed_ocr", 0),
                ocr_total=len(locals().get("to_ocr", [])),
                message="OCR 完成，正在匹配历史玩家",
            )

            # In slot mode, prefer per-slot joined text to avoid group-level line concatenation noise.
            raw_text = "\n".join(slot_texts if self.use_slots else (slot_texts or fallback_texts)).strip()
            match_text = raw_text
            match_result = None
            confirmation = _empty_confirmation(self.confirm_scans)
            published_alerts = False
            if raw_text:
                match_result = self.ocr_match_service.match_text(
                    raw_text=match_text,
                    threshold=self.threshold,
                    exclude_player_id=str(self.default_account_id or ""),
                    min_encounters=self.min_encounters,
                    require_tagged=self.require_tagged,
                )
                if publish_alerts and match_result.get("matches"):
                    confirmation = self._update_confirmation(match_result)
                    if confirmation["should_publish"]:
                        alerts_payload = self.alert_payload_builder(match_result)
                        alerts_payload["source"] = "auto_ocr"
                        alerts_payload["confirmation"] = confirmation
                        self.alerts_writer.write(alerts_payload)
                        self.events_repo.append_event("auto_ocr_alerts_published", alerts_payload)
                        published_alerts = True
                else:
                    self._reset_confirmation()
                self.events_repo.append_event(
                    "auto_ocr_roster_matched",
                    {
                        "match_count": match_result["match_count"],
                        "threshold": self.threshold,
                        "min_encounters": self.min_encounters,
                        "require_tagged": self.require_tagged,
                        "confirmation": confirmation,
                        "published_alerts": published_alerts,
                    },
                )
            else:
                self._reset_confirmation()

            completed_at = datetime.now(timezone.utc)
            status = {
                "enabled": bool(self._thread and self._thread.is_alive()),
                "state": "captured" if captures else "no_capture",
                "phase": "completed",
                "progress_percent": 100,
                "progress_message": "完成",
                "window_title": self.window_title,
                "process_name": self.process_name,
                "auto_focus": self.auto_focus,
                "debug_images": self.debug_images,
                "debug_dir": str(self.debug_dir),
                "debug_max_runs": self.debug_max_runs,
                "debug_run_id": captures[0].get("debug_run_id") if captures else None,
                "debug_run_dir": str(Path(captures[0]["debug_path"]).parent) if captures and captures[0].get("debug_path") else None,
                "focus_attempted": bool(captures and any(capture.get("focus_attempted") for capture in captures)),
                "focus_succeeded": bool(captures and all(capture.get("focus_succeeded") for capture in captures)),
                "process_id": captures[0].get("process_id") if captures else None,
                "foreground_process_id": captures[0].get("foreground_process_id") if captures else None,
                "window_match_mode": captures[0].get("window_match_mode") if captures else None,
                "capture_backend": "copy_from_screen_visible_pixels",
                "dpi_aware": bool(captures and all(capture.get("dpi_aware") for capture in captures)),
                "window_width": captures[0].get("window_width") if captures else None,
                "window_height": captures[0].get("window_height") if captures else None,
                "interval_seconds": self.interval_seconds,
                "configured_tesseract_path": self.configured_tesseract_path,
                "tesseract_path": self.tesseract_path,
                "tesseract_source": self.tesseract_source,
                "tesseract_checked_paths": self.tesseract_checked_paths,
                "requested_tesseract_lang": self.requested_tesseract_lang,
                "tesseract_lang": self.tesseract_lang,
                "tesseract_missing_langs": self.tesseract_missing_langs,
                "tesseract_tessdata_dir": self.tesseract_tessdata_dir,
                "tesseract_psm": self.tesseract_psm,
                "tesseract_oem": self.tesseract_oem,
                "image_scale": self.image_scale,
                "image_threshold": self.image_threshold,
                "confirm_scans": self.confirm_scans,
                "cooldown_seconds": self.cooldown_seconds,
                "tesseract_available": tesseract_available,
                "captures": captures,
                "archive_dir": str(archive_dir),
                "raw_text": raw_text,
                "slot_texts": slot_texts,
                "match_text": match_text,
                "match_result": match_result,
                "confirmation": confirmation,
                "published_alerts": published_alerts,
                "run_started_at": run_started_at.isoformat(),
                "capture_triggered_at": run_started_at.isoformat(),
                "capture_completed_at": capture_completed_at.isoformat(),
                "ocr_started_at": locals().get("ocr_started_at").isoformat() if locals().get("ocr_started_at") else None,
                "ocr_completed_at": match_started_at.isoformat(),
                "match_started_at": match_started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_seconds": round((completed_at - run_started_at).total_seconds(), 2),
                "updated_at": completed_at.isoformat(),
            }
            black_capture = _find_black_capture(captures)
            if black_capture:
                status["state"] = "black_frame_detected"
                status["black_frame_detected"] = True
                status["black_frame_capture"] = black_capture
                status["warning"] = (
                    "Captured Dota 2 pixels are black. This is usually caused by exclusive fullscreen "
                    "or a graphics capture path that Windows GDI CopyFromScreen cannot read. "
                    "Use Dota 2 borderless/windowed mode or switch to a Windows Graphics Capture backend."
                )
            if not tesseract_available:
                status["warning"] = (
                    "tesseract.exe not found; bundle tools/tesseract/tesseract.exe "
                    "or configure DOTA2_BOUNTY_TESSERACT_PATH to enable backend OCR."
                )
            self._write_status(status)
            return status

    def probe_full_window(self) -> dict[str, Any]:
        """Capture one full Dota 2 window frame without OCR or slot processing."""
        with self._lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            probe_dir = self.output_dir / "probe"
            probe_dir.mkdir(parents=True, exist_ok=True)
            output_path = probe_dir / "full-window.png"
            ocr_output_path = probe_dir / "full-window.ocr.png"
            region = {"name": "full-window", "kind": "probe_full", "x": 0, "y": 0, "w": 100, "h": 100}
            try:
                payload = _capture_region_with_legacy_gdi(
                    window_title=self.window_title,
                    process_name=self.process_name,
                    region=region,
                    output_path=output_path,
                    ocr_output_path=ocr_output_path,
                    scale=1,
                    threshold=0,
                )
            except Exception as exc:
                # Fallback for probe mode: use process-based capture path to recover when
                # title-based legacy enumeration does not resolve a visible target window.
                try:
                    payload = _capture_region_with_powershell(
                        window_title=self.window_title,
                        process_name=self.process_name,
                        auto_focus=True,
                        region=region,
                        output_path=output_path,
                        ocr_output_path=ocr_output_path,
                        scale=1,
                        threshold=0,
                        dpi_aware=True,
                        require_foreground=False,
                    )
                    payload["probe_fallback_reason"] = str(exc)
                except Exception as fallback_exc:
                    result = {
                        "enabled": bool(self._thread and self._thread.is_alive()),
                        "state": _classify_capture_error(str(fallback_exc)),
                        "error": str(fallback_exc),
                        "legacy_error": str(exc),
                        "window_title": self.window_title,
                        "process_name": self.process_name,
                        "capture_backend": "copy_from_screen_visible_pixels",
                        "capture_backend_variant": "gdi_legacy_title",
                        "powershell_path": _powershell_executable(),
                        "path": str(output_path),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    self.runtime_status.merge({"auto_ocr_probe": result})
                    return result

            payload["path"] = str(output_path)
            payload["ocr_path"] = str(ocr_output_path)
            payload["kind"] = "probe_full"
            payload["capture_backend"] = "copy_from_screen_visible_pixels"
            if payload.get("probe_fallback_reason"):
                payload["capture_backend_variant"] = "process_window_fallback"
            else:
                payload.setdefault("capture_backend_variant", "gdi_legacy_title")
            payload["powershell_path"] = _powershell_executable()
            payload["enabled"] = bool(self._thread and self._thread.is_alive())
            payload["state"] = "black_frame_detected" if _is_black_capture_payload(payload) else "captured"
            payload["black_frame_detected"] = payload["state"] == "black_frame_detected"
            archive_dir = self._archive_probe_image(Path(output_path), Path(ocr_output_path))
            payload["archive_dir"] = str(archive_dir)
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.runtime_status.merge({"auto_ocr_probe": payload})
            return payload

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once(publish_alerts=True)
            except Exception as exc:  # pragma: no cover - defensive background worker
                self._write_status(
                    {
                        "enabled": True,
                        "state": "error",
                        "error": str(exc),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            self._stop.wait(self.interval_seconds)

    def _write_status(self, status: dict[str, Any]) -> None:
        self._last_status = dict(status)
        captures = status.get("captures")
        if status.get("state") in {"captured", "captured_only"} and (
            captures or status.get("raw_text") or status.get("match_result")
        ):
            self._last_capture_status = dict(status)
        self.runtime_status.merge({"auto_ocr": status})

    def _write_progress(
        self,
        *,
        state: str,
        phase: str,
        progress_percent: int,
        message: str,
        run_started_at: datetime,
        capture_triggered_at: datetime | None = None,
        capture_completed_at: datetime | None = None,
        ocr_started_at: datetime | None = None,
        ocr_completed_at: datetime | None = None,
        ocr_current: int | None = None,
        ocr_total: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        progress = {
            **self.status(),
            "enabled": bool(self._thread and self._thread.is_alive()),
            "state": state,
            "phase": phase,
            "progress_percent": max(0, min(100, int(progress_percent))),
            "progress_message": message,
            "run_started_at": run_started_at.isoformat(),
            "capture_triggered_at": capture_triggered_at.isoformat() if capture_triggered_at else None,
            "capture_completed_at": capture_completed_at.isoformat() if capture_completed_at else None,
            "ocr_started_at": ocr_started_at.isoformat() if ocr_started_at else None,
            "ocr_completed_at": ocr_completed_at.isoformat() if ocr_completed_at else None,
            "ocr_current": ocr_current,
            "ocr_total": ocr_total,
            "elapsed_seconds": round((now - run_started_at).total_seconds(), 1),
            "updated_at": now.isoformat(),
        }
        # Keep previous screenshot/OCR details while reporting progress.
        if self._last_capture_status:
            progress.setdefault("captures", self._last_capture_status.get("captures"))
            progress.setdefault("raw_text", self._last_capture_status.get("raw_text"))
            progress.setdefault("match_result", self._last_capture_status.get("match_result"))
        self._last_status = progress
        self.runtime_status.merge({"auto_ocr": progress})

    def _update_confirmation(self, match_result: dict[str, Any]) -> dict[str, Any]:
        hit_key = _build_hit_key(match_result)
        if not hit_key:
            self._reset_confirmation()
            return _empty_confirmation(self.confirm_scans)
        if hit_key == self._stable_hit_key:
            self._stable_hit_count += 1
        else:
            self._stable_hit_key = hit_key
            self._stable_hit_count = 1
        now = datetime.now(timezone.utc)
        last_published = self._last_published_hits.get(hit_key)
        cooldown_remaining = 0.0
        if last_published:
            elapsed = (now - last_published).total_seconds()
            cooldown_remaining = max(0.0, self.cooldown_seconds - elapsed)
        confirmed = self._stable_hit_count >= self.confirm_scans
        should_publish = confirmed and cooldown_remaining <= 0
        if should_publish:
            self._last_published_hits[hit_key] = now
        return {
            "hit_key": hit_key,
            "stable_hit_count": self._stable_hit_count,
            "required_scans": self.confirm_scans,
            "confirmed": confirmed,
            "cooldown_remaining_seconds": round(cooldown_remaining, 1),
            "should_publish": should_publish,
        }

    def _reset_confirmation(self) -> None:
        self._stable_hit_key = None
        self._stable_hit_count = 0

    def _capture_regions(self) -> list[dict[str, Any]]:
        regions = _build_auto_ocr_regions(include_slots=self.use_slots)
        captures = []
        run_debug_dir: Path | None = None
        debug_run_id = ""
        full_capture: dict[str, Any] | None = None
        if self.debug_images:
            debug_run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            run_debug_dir = self.debug_dir / debug_run_id
            run_debug_dir.mkdir(parents=True, exist_ok=True)
            try:
                full_capture = self._capture_full_debug_window(run_debug_dir=run_debug_dir, run_id=debug_run_id)
            except Exception as exc:
                (run_debug_dir / "error.txt").write_text(
                    "\n".join(
                        [
                            f"failed_at={datetime.now(timezone.utc).isoformat()}",
                            f"error={exc}",
                            "No screenshots were saved because full-window capture failed.",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self._cleanup_debug_runs()
                raise
        for region in regions:
            path = self.output_dir / f"{region['name']}.png"
            ocr_path = self.output_dir / f"{region['name']}.ocr.png"
            payload = _capture_region_with_gdi_fallback(
                window_title=self.window_title,
                process_name=self.process_name,
                auto_focus=self.auto_focus,
                require_foreground=True,
                region=region,
                output_path=path,
                ocr_output_path=ocr_path,
                scale=region.get("scale") if region.get("scale") is not None else self.image_scale,
                threshold=region.get("threshold") if region.get("threshold") is not None else self.image_threshold,
            )
            payload["path"] = str(path)
            payload["ocr_path"] = str(ocr_path)
            payload["kind"] = region.get("kind")
            payload["team"] = region.get("team")
            payload["slot_index"] = region.get("slot_index")
            payload["slot_name"] = region.get("slot_name")
            if run_debug_dir:
                debug_path = run_debug_dir / f"{region['name']}.png"
                debug_ocr_path = run_debug_dir / f"{region['name']}.ocr.png"
                shutil.copy2(path, debug_path)
                shutil.copy2(ocr_path, debug_ocr_path)
                payload["debug_path"] = str(debug_path)
                payload["debug_ocr_path"] = str(debug_ocr_path)
                payload["debug_run_id"] = debug_run_id
            captures.append(payload)
        if run_debug_dir and full_capture:
            annotated_path = run_debug_dir / "full-window.annotated.png"
            _annotate_debug_full_window(
                source_path=Path(full_capture["path"]),
                output_path=annotated_path,
                captures=captures,
            )
            (run_debug_dir / "complete.txt").write_text(
                f"completed_at={datetime.now(timezone.utc).isoformat()}\n",
                encoding="utf-8",
            )
            for capture in captures:
                capture["debug_full_window_path"] = full_capture["path"]
                capture["debug_annotated_path"] = str(annotated_path)
            self._cleanup_debug_runs()
        return captures

    def _capture_regions_capture_only(self) -> list[dict[str, Any]]:
        # Lightweight test mode: keep just full/left/right captures so manual review is fast.
        regions = [
            {"name": "full-window", "kind": "debug_full", "x": 0, "y": 0, "w": 100, "h": 100, "scale": 1, "threshold": 0},
            {"name": "left5", "kind": "group", "team": "radiant", "x": TOP_BAR_LEFT_X, "y": TOP_BAR_Y, "w": TOP_BAR_W, "h": TOP_BAR_H, "scale": None, "threshold": None},
            {"name": "right5", "kind": "group", "team": "dire", "x": TOP_BAR_RIGHT_X, "y": TOP_BAR_Y, "w": TOP_BAR_W, "h": TOP_BAR_H, "scale": None, "threshold": None},
        ]
        captures = []
        for region in regions:
            path = self.output_dir / f"{region['name']}.png"
            ocr_path = self.output_dir / f"{region['name']}.ocr.png"
            payload = _capture_region_with_gdi_fallback(
                window_title=self.window_title,
                process_name=self.process_name,
                auto_focus=True,
                require_foreground=True,
                region=region,
                output_path=path,
                ocr_output_path=ocr_path,
                scale=region.get("scale") if region.get("scale") is not None else self.image_scale,
                threshold=region.get("threshold") if region.get("threshold") is not None else self.image_threshold,
            )
            payload["path"] = str(path)
            payload["ocr_path"] = str(ocr_path)
            payload["kind"] = region.get("kind")
            payload["team"] = region.get("team")
            captures.append(payload)
        return captures

    def _capture_full_debug_window(self, *, run_debug_dir: Path, run_id: str) -> dict[str, Any]:
        full_region = {"name": "full-window", "kind": "debug_full", "x": 0, "y": 0, "w": 100, "h": 100}
        latest_path = self.output_dir / "full-window.png"
        latest_ocr_path = self.output_dir / "full-window.ocr.png"
        payload = _capture_region_with_gdi_fallback(
            window_title=self.window_title,
            process_name=self.process_name,
            auto_focus=self.auto_focus,
            require_foreground=True,
            region=full_region,
            output_path=latest_path,
            ocr_output_path=latest_ocr_path,
            scale=1,
            threshold=0,
        )
        debug_path = run_debug_dir / "full-window.png"
        shutil.copy2(latest_path, debug_path)
        payload["path"] = str(debug_path)
        payload["latest_path"] = str(latest_path)
        payload["kind"] = "debug_full"
        payload["debug_run_id"] = run_id
        return payload

    def _cleanup_debug_runs(self) -> None:
        if not self.debug_dir.exists():
            return
        runs = [path for path in self.debug_dir.iterdir() if path.is_dir()]
        runs.sort(key=lambda path: path.name, reverse=True)
        for stale in runs[self.debug_max_runs:]:
            shutil.rmtree(stale, ignore_errors=True)

    def _archive_probe_image(self, image_path: Path, ocr_path: Path) -> Path:
        archive_dir = self.output_dir / "probe" / "archive" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        archive_dir.mkdir(parents=True, exist_ok=True)
        if image_path.exists():
            shutil.copy2(image_path, archive_dir / image_path.name)
        if ocr_path.exists():
            shutil.copy2(ocr_path, archive_dir / ocr_path.name)
        return archive_dir

    def _archive_test_images(self, *, captures: list[dict[str, Any]], mode: str) -> Path:
        archive_dir = self.output_dir / "test-captures" / f"{mode}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for capture in captures:
            path_value = capture.get("path")
            if not path_value:
                continue
            src = Path(path_value)
            if src.exists():
                shutil.copy2(src, archive_dir / src.name)
            ocr_value = capture.get("ocr_path")
            if ocr_value:
                ocr_src = Path(ocr_value)
                if ocr_src.exists():
                    shutil.copy2(ocr_src, archive_dir / ocr_src.name)
        return archive_dir

    def _ocr_image(self, path: Path, *, raw_path: Path | None = None, is_slot: bool = False) -> str:
        if is_slot:
            whitelist = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-.=+[]() "
            candidates: list[str] = []
            candidates.append(
                self._run_tesseract(image_path=path, psm=8, whitelist=whitelist, lang=self._slot_primary_lang())
            )
            if self._slot_should_try_multilang():
                # Chinese nicknames need a no-whitelist path. Keep it in the slot path
                # so the English fast path cannot suppress Chinese text.
                candidates.append(
                    self._run_tesseract(image_path=path, psm=7, whitelist=None, lang=self.tesseract_lang)
                )
            if raw_path and raw_path.exists():
                candidates.append(
                    self._run_tesseract(image_path=raw_path, psm=8, whitelist=whitelist, lang=self._slot_primary_lang())
                )
                if self._slot_should_try_multilang():
                    candidates.append(
                        self._run_tesseract(image_path=raw_path, psm=7, whitelist=None, lang=self.tesseract_lang)
                    )
            normalized_candidates = [_normalize_slot_text(item) for item in candidates]
            normalized_candidates = [item for item in normalized_candidates if item]
            if not normalized_candidates:
                return ""
            return max(normalized_candidates, key=_score_slot_candidate)

        text = self._run_tesseract(image_path=path, psm=7, whitelist=None, lang=self.tesseract_lang)
        if text:
            return text
        if raw_path and raw_path.exists():
            return self._run_tesseract(image_path=raw_path, psm=7, whitelist=None, lang=self.tesseract_lang)
        return ""

    def _ocr_capture_text(self, capture: dict[str, Any]) -> str:
        ocr_path = capture.get("ocr_path")
        source_path = capture.get("path")
        if not source_path and not ocr_path:
            return ""
        return self._ocr_image(
            Path(ocr_path or source_path),
            raw_path=Path(source_path) if source_path else None,
            is_slot=capture.get("kind") == "slot",
        )

    def _ocr_capture_text_hq(self, capture: dict[str, Any]) -> str:
        ocr_path = capture.get("ocr_path")
        source_path = capture.get("path")
        primary = Path(ocr_path or source_path) if (ocr_path or source_path) else None
        if primary is None:
            return ""
        # High-quality fallback: broader language and line segmentation.
        text = self._run_tesseract(image_path=primary, psm=7, whitelist=None, lang=self.tesseract_lang)
        if text:
            return _normalize_slot_text(text)
        if source_path:
            text = self._run_tesseract(image_path=Path(source_path), psm=7, whitelist=None, lang=self.tesseract_lang)
            if text:
                return _normalize_slot_text(text)
        return ""

    def _repair_slot_lines_with_candidates(self, slot_lines: list[str]) -> list[str]:
        try:
            candidates = self.ocr_match_service.players_repo.list_name_match_candidates(limit=600)
        except Exception:
            return slot_lines
        aliases: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            names = []
            latest_name = candidate.get("latest_name")
            if latest_name:
                names.append(str(latest_name))
            encounter_names = candidate.get("encounter_names")
            if encounter_names:
                names.extend([str(x) for x in str(encounter_names).split(",") if x])
            for n in names:
                cleaned = _clean_name_for_repair(n)
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    aliases.append(n.strip())
        if not aliases:
            return slot_lines

        repaired: list[str] = []
        for line in slot_lines:
            line_clean = _clean_name_for_repair(line)
            if len(line_clean) < 3:
                repaired.append(line)
                continue
            best_name = line
            best_score = 0.0
            for alias in aliases:
                score = SequenceMatcher(None, _clean_name_for_repair(alias), line_clean).ratio()
                if score > best_score:
                    best_score = score
                    best_name = alias
            # Only replace when similarity is strong enough.
            if best_score >= 0.82:
                repaired.append(best_name)
            else:
                repaired.append(line)
        return repaired

    def _run_tesseract(self, *, image_path: Path, psm: int, whitelist: str | None, lang: str) -> str:
        command = [
            self.tesseract_path,
            str(image_path),
            "stdout",
            "-l",
            lang,
            "--psm",
            str(psm),
            "--oem",
            str(self.tesseract_oem),
        ]
        if whitelist:
            command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=3,
        )
        if completed.returncode != 0:
            return ""
        stdout_bytes = completed.stdout or b""
        text_out = _decode_tesseract_stdout(stdout_bytes)
        return _normalize_ocr_text(text_out)

    def _slot_primary_lang(self) -> str:
        # For most nickname strings, Latin model is both faster and cleaner.
        # Keep full configured language as fallback when needed.
        if "+" in (self.tesseract_lang or ""):
            return "eng"
        return self.tesseract_lang or "eng"

    def _slot_should_try_multilang(self) -> bool:
        return "chi_sim" in (self.tesseract_lang or "")

    def _tesseract_available(self) -> bool:
        configured = Path(self.tesseract_path)
        if configured.exists():
            return True
        return shutil.which(self.tesseract_path) is not None


def _build_auto_ocr_regions(*, include_slots: bool) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = [
        {"name": "left5", "kind": "group", "team": "radiant", "x": TOP_BAR_LEFT_X, "y": TOP_BAR_Y, "w": TOP_BAR_W, "h": TOP_BAR_H},
        {"name": "right5", "kind": "group", "team": "dire", "x": TOP_BAR_RIGHT_X, "y": TOP_BAR_Y, "w": TOP_BAR_W, "h": TOP_BAR_H},
    ]
    if include_slots:
        regions.extend(_build_slot_regions(team="radiant", start_x=TOP_BAR_LEFT_X, y=TOP_BAR_Y, total_w=TOP_BAR_W, h=TOP_BAR_H))
        regions.extend(_build_slot_regions(team="dire", start_x=TOP_BAR_RIGHT_X, y=TOP_BAR_Y, total_w=TOP_BAR_W, h=TOP_BAR_H))
    return regions


def _normalize_ocr_text(text: str) -> str:
    raw = (text or "").replace("\r", "")
    cleaned_lines: list[str] = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        # Drop obvious local URL noise captured from overlays/dev pages.
        if "127.0.0.1" in s or "/debug/ocr/" in s.lower():
            continue
        # Keep common nickname characters; trim punctuation-heavy garbage.
        s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-=\+\.\[\]\(\)\s]", " ", s)
        s = re.sub(r"\s{2,}", " ", s).strip()
        if s:
            cleaned_lines.append(s)
    return "\n".join(cleaned_lines).strip()


def _normalize_slot_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"^[_\-\.\s]+", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s)
    # Common OCR ambiguity normalization for player names.
    has_alpha = bool(re.search(r"[A-Za-z]", s))
    if has_alpha:
        s = s.replace("0", "o").replace("1", "l").replace("5", "s")
    s = s.replace("|", "l")
    s = re.sub(r"\brn\b", "m", s, flags=re.IGNORECASE)
    # Heuristic: discard ultra-short noisy fragments.
    if len(s) == 1 and s.lower() not in {"i", "l"}:
        return ""
    short_map = {
        "tu": "Tú",
        "fu": "Tú",
        "tii": "Tú",
        "ua": "Tú",
        "ta": "Tú",
        "tv": "Tú",
        "wa": "Tú",
    }
    low = s.lower()
    if low in short_map:
        return short_map[low]
    typo_map = {
        "inh": "Linh",
        "linhh": "Linh",
        "bus": "Luis",
        "luls": "Luis",
        "pernitle": "Pernille",
        "pernile": "Pernille",
    }
    if low in typo_map:
        return typo_map[low]
    return s


def _score_ocr_text(text: str) -> int:
    if not text:
        return -1
    score = 0
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        score += min(len(s), 24)
        if re.search(r"[A-Za-z\u4e00-\u9fff]", s):
            score += 6
        if re.search(r"^\d+$", s):
            score -= 4
        if len(s) <= 1:
            score -= 6
    return score


def _score_slot_candidate(text: str) -> int:
    s = (text or "").strip()
    if not s:
        return -100
    score = _score_ocr_text(s)
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", s))
    latin_count = len(re.findall(r"[A-Za-z]", s))
    digit_count = len(re.findall(r"\d", s))
    score += chinese_count * 12
    score += min(latin_count, 12)
    score += min(digit_count, 6)
    if _is_weak_slot_text(s):
        score -= 18
    if len(s) > 24:
        score -= len(s) - 24
    return score


def _is_weak_slot_text(text: str) -> bool:
    s = (text or "").strip()
    if len(s) <= 1:
        return True
    if re.fullmatch(r"[eEisS\|lI\.\-_=]+", s):
        return True
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", s):
        return True
    return False


def _needs_slot_quality_fallback(slot_captures: list[dict[str, Any]]) -> bool:
    if not slot_captures:
        return False
    texts = [str(c.get("text") or "") for c in slot_captures]
    weak = sum(1 for t in texts if _is_weak_slot_text(t))
    return weak >= max(4, len(texts) // 2)


def _clean_name_for_repair(value: str) -> str:
    lowered = str(value or "").casefold().strip()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", lowered)


def _build_slot_regions(*, team: str, start_x: float, y: float, total_w: float, h: float) -> list[dict[str, Any]]:
    slot_w = total_w / 5
    horizontal_padding = min(0.35, slot_w * 0.08)
    regions = []
    for index in range(5):
        x = start_x + index * slot_w + horizontal_padding
        w = slot_w - horizontal_padding * 2
        slot_index = index + 1
        regions.append(
            {
                "name": f"slot-{team}-{slot_index}",
                "kind": "slot",
                "team": team,
                "slot_index": slot_index,
                "slot_name": f"{team}{slot_index}",
                "x": round(x, 3),
                # Keep slot crop a bit tighter vertically to reduce non-name HUD noise.
                "y": round(y + 0.05, 3),
                "w": round(w, 3),
                "h": round(max(1.8, h - 0.35), 3),
                # Slot-specific OCR preprocessing tuned for tiny nicknames on dark background.
                "scale": 6,
                "threshold": 0,
            }
        )
    return regions


def _decode_tesseract_stdout(raw: bytes) -> str:
    if not raw:
        return ""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "cp936"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _build_hit_key(match_result: dict[str, Any]) -> str:
    matches = match_result.get("matches") or []
    player_ids = [
        str(item.get("matched_player_id") or "")
        for item in matches
        if item.get("matched_player_id")
    ]
    return "|".join(sorted(set(player_ids)))


def _empty_confirmation(required_scans: int) -> dict[str, Any]:
    return {
        "hit_key": "",
        "stable_hit_count": 0,
        "required_scans": required_scans,
        "confirmed": False,
        "cooldown_remaining_seconds": 0.0,
        "should_publish": False,
    }


def _find_black_capture(captures: list[dict[str, Any]]) -> dict[str, Any] | None:
    for capture in captures:
        near_black = capture.get("near_black_percent")
        avg_gray = capture.get("average_gray")
        if near_black is None or avg_gray is None:
            continue
        if float(near_black) >= 98.0 and float(avg_gray) <= 3.0:
            return {
                "name": capture.get("name"),
                "path": capture.get("path"),
                "debug_path": capture.get("debug_path"),
                "debug_full_window_path": capture.get("debug_full_window_path"),
                "average_gray": avg_gray,
                "near_black_percent": near_black,
            }
    return None


def _resolve_tesseract_path(configured_path: str) -> dict[str, Any]:
    checked_paths: list[str] = []
    bundled_candidates = [
        BACKEND_DIR / "tools" / "tesseract" / "tesseract.exe",
        BACKEND_DIR.parent / "tools" / "tesseract" / "tesseract.exe",
    ]
    for candidate in bundled_candidates:
        checked_paths.append(str(candidate))
        if candidate.exists():
            return {"path": str(candidate), "source": "bundled", "checked_paths": checked_paths}

    if configured_path:
        configured = Path(configured_path)
        checked_paths.append(configured_path)
        if configured.exists():
            return {"path": configured_path, "source": "configured", "checked_paths": checked_paths}
        found = shutil.which(configured_path)
        if found:
            checked_paths.append(found)
            return {"path": found, "source": "path", "checked_paths": checked_paths}

    for candidate in (
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ):
        checked_paths.append(str(candidate))
        if candidate.exists():
            return {"path": str(candidate), "source": "default_install", "checked_paths": checked_paths}

    fallback = configured_path or "tesseract"
    return {"path": fallback, "source": "missing", "checked_paths": checked_paths}


def _bounded_int(value: int | str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _resolve_tesseract_lang(tesseract_path: str, requested_lang: str) -> dict[str, Any]:
    requested = [item.strip() for item in str(requested_lang or "eng").split("+") if item.strip()]
    if not requested:
        requested = ["eng"]

    tessdata_dir = Path(tesseract_path).resolve().parent / "tessdata"
    available = {
        path.stem
        for path in tessdata_dir.glob("*.traineddata")
    } if tessdata_dir.exists() else set()
    if not available:
        return {
            "lang": "+".join(requested),
            "missing_langs": [],
            "tessdata_dir": str(tessdata_dir),
        }

    present = [lang for lang in requested if lang in available]
    missing = [lang for lang in requested if lang not in available]
    if not present and "eng" in available:
        present = ["eng"]
    if not present:
        present = requested
    return {
        "lang": "+".join(present),
        "missing_langs": missing,
        "tessdata_dir": str(tessdata_dir),
    }


def _classify_capture_error(error: str) -> str:
    if "DOTA_NOT_FOREGROUND" in error:
        return "dota_not_foreground"
    if "DOTA_MINIMIZED" in error:
        return "dota_minimized"
    if "DOTA_WINDOW_TOO_SMALL" in error:
        return "dota_window_too_small"
    if "Window not found" in error:
        return "window_not_found"
    return "capture_failed"


def _capture_error_details(error: str) -> dict[str, Any]:
    details: dict[str, Any] = {
        "state": _classify_capture_error(error),
        "focus_attempted": _parse_bool_token(error, "auto_focus"),
        "focus_succeeded": _parse_bool_token(error, "focus_succeeded"),
        "window_match_mode": _parse_text_token(error, "window_match_mode"),
    }
    process_id = _parse_int_token(error, "pid")
    foreground_process_id = _parse_int_token(error, "foreground_pid")
    if process_id is not None:
        details["process_id"] = process_id
    if foreground_process_id is not None:
        details["foreground_process_id"] = foreground_process_id
    return details


def _parse_int_token(text: str, token: str) -> int | None:
    match = re.search(rf"\b{re.escape(token)}=(\d+)\b", text)
    if not match:
        return None
    return int(match.group(1))


def _parse_bool_token(text: str, token: str) -> bool:
    match = re.search(rf"\b{re.escape(token)}=(True|False|true|false|1|0)\b", text)
    if not match:
        return False
    return match.group(1).lower() in {"true", "1"}


def _parse_text_token(text: str, token: str) -> str | None:
    match = re.search(rf"\b{re.escape(token)}=([A-Za-z0-9_.-]+)\b", text)
    if not match:
        return None
    return match.group(1)


def _normalize_process_identity(process_name: str) -> tuple[str, str]:
    raw = (process_name or "dota2.exe").strip()
    if not raw:
        raw = "dota2.exe"
    exe_name = raw if raw.lower().endswith(".exe") else f"{raw}.exe"
    base_name = exe_name[:-4]
    return exe_name, base_name


def _annotate_debug_full_window(
    *,
    source_path: Path,
    output_path: Path,
    captures: list[dict[str, Any]],
) -> None:
    rect_lines = []
    for capture in captures:
        x = capture.get("x")
        y = capture.get("y")
        w = capture.get("w")
        h = capture.get("h")
        if None in {x, y, w, h}:
            continue
        name = str(capture.get("name") or "")
        color = "Lime" if capture.get("kind") == "slot" else "Yellow"
        rect_lines.append(
            f"$rects += [pscustomobject]@{{X={int(x)};Y={int(y)};W={int(w)};H={int(h)};Name={name!r};Color={color!r}}}"
        )
    rect_script = "\n".join(rect_lines)
    script = rf"""
Add-Type -AssemblyName System.Drawing
$source = {str(source_path)!r}
$output = {str(output_path)!r}
$image = [System.Drawing.Image]::FromFile($source)
$bitmap = New-Object System.Drawing.Bitmap($image)
$image.Dispose()
$g = [System.Drawing.Graphics]::FromImage($bitmap)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$font = New-Object System.Drawing.Font("Consolas", 18, [System.Drawing.FontStyle]::Bold)
$rects = @()
{rect_script}
foreach ($rect in $rects) {{
  $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromName($rect.Color), 3)
  $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(210, 0, 0, 0))
  $textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromName($rect.Color))
  $g.DrawRectangle($pen, $rect.X, $rect.Y, $rect.W, $rect.H)
  $label = [string]$rect.Name
  $size = $g.MeasureString($label, $font)
  $g.FillRectangle($brush, $rect.X, [Math]::Max(0, $rect.Y - [int]$size.Height), [int]$size.Width + 8, [int]$size.Height)
  $g.DrawString($label, $font, $textBrush, $rect.X + 4, [Math]::Max(0, $rect.Y - [int]$size.Height))
  $pen.Dispose()
  $brush.Dispose()
  $textBrush.Dispose()
}}
$stream = [System.IO.File]::Open($output, [System.IO.FileMode]::Create)
$bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
$stream.Dispose()
$font.Dispose()
$g.Dispose()
$bitmap.Dispose()
"""
    completed = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(_safe_text(completed.stderr).strip() or _safe_text(completed.stdout).strip() or "debug annotation failed")


def _capture_region_with_gdi_fallback(
    *,
    window_title: str,
    process_name: str,
    auto_focus: bool,
    require_foreground: bool,
    region: dict[str, Any],
    output_path: Path,
    ocr_output_path: Path,
    scale: int,
    threshold: int,
) -> dict[str, Any]:
    # Proven path on this machine: main window handle + DPI aware + CopyFromScreen.
    process_main_error = ""
    try:
        process_main = _capture_region_with_process_mainwindow_gdi(
            process_name=process_name,
            auto_focus=auto_focus,
            require_foreground=require_foreground,
            region=region,
            output_path=output_path,
            ocr_output_path=ocr_output_path,
            scale=scale,
            threshold=threshold,
        )
        if require_foreground and not bool(process_main.get("focus_succeeded")):
            raise RuntimeError("DOTA_NOT_FOREGROUND: process main window is not foreground")
        process_main["capture_fallback_used"] = False
        process_main["capture_backend_variant"] = "process_mainwindow_gdi"
        if not _is_black_capture_payload(process_main):
            return process_main
    except Exception as exc:
        process_main_error = str(exc)

    # Preferred path: process-window capture with DPI awareness.
    # This gives correct physical-resolution coordinates on HiDPI/2K displays
    # and is more robust than title-only legacy matching.
    process_dpi_error = ""
    try:
        process_dpi = _capture_region_with_powershell(
            window_title=window_title,
            process_name=process_name,
            auto_focus=auto_focus,
            region=region,
            output_path=output_path,
            ocr_output_path=ocr_output_path,
            scale=scale,
            threshold=threshold,
            dpi_aware=True,
            require_foreground=require_foreground,
        )
        process_dpi["capture_fallback_used"] = False
        process_dpi["capture_backend_variant"] = "process_window_dpi"
        if process_main_error:
            process_dpi["primary_error"] = process_main_error
        if not _is_black_capture_payload(process_dpi):
            return process_dpi
    except Exception as exc:
        process_dpi_error = str(exc)

    # Secondary path: process-window capture without DPI awareness.
    process_nodpi_error = ""
    try:
        process_nodpi = _capture_region_with_powershell(
            window_title=window_title,
            process_name=process_name,
            auto_focus=auto_focus,
            region=region,
            output_path=output_path,
            ocr_output_path=ocr_output_path,
            scale=scale,
            threshold=threshold,
            dpi_aware=False,
            require_foreground=require_foreground,
        )
        process_nodpi["capture_fallback_used"] = True
        process_nodpi["capture_backend_variant"] = "process_window_no_dpi"
        process_nodpi["capture_fallback_reason"] = "dpi_aware_black_or_failed"
        if process_dpi_error:
            process_nodpi["primary_error"] = process_dpi_error
        if not _is_black_capture_payload(process_nodpi):
            return process_nodpi
    except Exception as exc:
        process_nodpi_error = str(exc)

    # Keep legacy title path as the final fallback.
    try:
        legacy = _capture_region_with_legacy_gdi(
            window_title=window_title,
            process_name=process_name,
            region=region,
            output_path=output_path,
            ocr_output_path=ocr_output_path,
            scale=scale,
            threshold=threshold,
        )
        legacy["capture_fallback_used"] = False
        legacy["capture_backend_variant"] = "gdi_legacy_title"
        if require_foreground:
            legacy["primary_error"] = (
                "DOTA_NOT_FOREGROUND: legacy title fallback disabled when foreground is required"
            )
            raise RuntimeError("DOTA_NOT_FOREGROUND: legacy fallback disabled")
        if not _is_black_capture_payload(legacy):
            if process_dpi_error:
                legacy["primary_error"] = process_dpi_error
            if process_nodpi_error:
                legacy["secondary_error"] = process_nodpi_error
            return legacy
    except Exception:
        legacy = None

    # Final compatibility fallback.
    fallback = _capture_region_with_powershell(
        window_title=window_title,
        process_name=process_name,
        auto_focus=auto_focus,
        region=region,
        output_path=output_path,
        ocr_output_path=ocr_output_path,
        scale=scale,
        threshold=threshold,
        dpi_aware=False,
        require_foreground=require_foreground,
    )
    fallback["capture_fallback_used"] = True
    fallback["capture_backend_variant"] = "compatibility_fallback"
    fallback["capture_fallback_reason"] = "all_primary_paths_black_or_failed"
    if process_dpi_error:
        fallback["primary_error"] = process_dpi_error
    if process_main_error:
        fallback["mainwindow_error"] = process_main_error
    if process_nodpi_error:
        fallback["secondary_error"] = process_nodpi_error
    return fallback


def _capture_region_with_process_mainwindow_gdi(
    *,
    process_name: str,
    auto_focus: bool,
    require_foreground: bool,
    region: dict[str, Any],
    output_path: Path,
    ocr_output_path: Path,
    scale: int,
    threshold: int,
) -> dict[str, Any]:
    process_exe, process_base = _normalize_process_identity(process_name)
    script = rf"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32MainCapture {{
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
}}
public struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}
"@
[void][Win32MainCapture]::SetProcessDPIAware()
$targetProcessExe = {process_exe!r}
$targetProcessBase = {process_base!r}
$autoFocus = {"$true" if auto_focus else "$false"}
$requireForeground = {"$true" if require_foreground else "$false"}
$process = Get-Process -ErrorAction SilentlyContinue |
  Where-Object {{
    $_.MainWindowHandle -ne 0 -and (
      $_.ProcessName -ieq $targetProcessBase -or
      ($_.Path -and [System.IO.Path]::GetFileName($_.Path) -ieq $targetProcessExe)
    )
  }} |
  Sort-Object StartTime -Descending |
  Select-Object -First 1
if (-not $process) {{ throw "Window not found: process=$targetProcessExe" }}
$targetPid = [uint32]$process.Id
$hWnd = $process.MainWindowHandle
if (-not $hWnd -or $hWnd -eq 0) {{ throw "Window not found: process=$targetProcessExe main_window_handle=0 pid=$targetPid" }}
$focusAttempted = $false
$focusSucceeded = $false
if ($autoFocus) {{
  $focusAttempted = $true
  [void][Win32MainCapture]::ShowWindow($hWnd, 9)
  Start-Sleep -Milliseconds 120
  [void][Win32MainCapture]::SetForegroundWindow($hWnd)
  Start-Sleep -Milliseconds 220
}}
$fg = [Win32MainCapture]::GetForegroundWindow()
[uint32]$fgPid = 0
if ($fg -ne [IntPtr]::Zero) {{
  [void][Win32MainCapture]::GetWindowThreadProcessId($fg, [ref]$fgPid)
}}
$focusSucceeded = ($fg -eq $hWnd) -or ($fgPid -eq $targetPid)
if ($requireForeground -and -not $focusSucceeded) {{
  throw "DOTA_NOT_FOREGROUND: process=$targetProcessExe pid=$targetPid foreground_pid=$fgPid window_match_mode=process auto_focus=$focusAttempted focus_succeeded=$focusSucceeded"
}}
$rect = New-Object RECT
[void][Win32MainCapture]::GetWindowRect($hWnd, [ref]$rect)
$winW = $rect.Right - $rect.Left
$winH = $rect.Bottom - $rect.Top
if ($winW -lt 640 -or $winH -lt 360) {{ throw "DOTA_WINDOW_TOO_SMALL: process=$targetProcessExe pid=$targetPid width=$winW height=$winH" }}
$x = [Math]::Floor($winW * {region['x']} / 100)
$y = [Math]::Floor($winH * {region['y']} / 100)
$w = [Math]::Max(1, [Math]::Floor($winW * {region['w']} / 100))
$h = [Math]::Max(1, [Math]::Floor($winH * {region['h']} / 100))
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($rect.Left + $x, $rect.Top + $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$g.Dispose()
$sampleStepX = [Math]::Max(1, [Math]::Floor($w / 80))
$sampleStepY = [Math]::Max(1, [Math]::Floor($h / 45))
$sampleCount = 0
$graySum = 0
$nearBlackCount = 0
for ($sampleY = 0; $sampleY -lt $h; $sampleY += $sampleStepY) {{
  for ($sampleX = 0; $sampleX -lt $w; $sampleX += $sampleStepX) {{
    $sampleColor = $bmp.GetPixel($sampleX, $sampleY)
    $sampleGray = [int](($sampleColor.R * 0.299) + ($sampleColor.G * 0.587) + ($sampleColor.B * 0.114))
    $graySum += $sampleGray
    $sampleCount += 1
    if ($sampleGray -lt 8) {{ $nearBlackCount += 1 }}
  }}
}}
$avgGray = 0
$nearBlackPercent = 0
if ($sampleCount -gt 0) {{
  $avgGray = [Math]::Round($graySum / $sampleCount, 2)
  $nearBlackPercent = [Math]::Round(100 * $nearBlackCount / $sampleCount, 2)
}}
$rawStream = [System.IO.File]::Open({str(output_path)!r}, [System.IO.FileMode]::Create)
$bmp.Save($rawStream, [System.Drawing.Imaging.ImageFormat]::Png)
$rawStream.Dispose()
$scale = [Math]::Max(1, {scale})
$threshold = [Math]::Max(0, [Math]::Min(255, {threshold}))
if ($scale -eq 1 -and $threshold -eq 0) {{
  $ocrStream = [System.IO.File]::Open({str(ocr_output_path)!r}, [System.IO.FileMode]::Create)
  $bmp.Save($ocrStream, [System.Drawing.Imaging.ImageFormat]::Png)
  $ocrStream.Dispose()
  $bmp.Dispose()
  Write-Output "$winW,$winH,$x,$y,$w,$h,process,$targetPid,$fgPid,$focusAttempted,$focusSucceeded,$avgGray,$nearBlackPercent"
  return
}}
$ocrW = [Math]::Max(1, $w * $scale)
$ocrH = [Math]::Max(1, $h * $scale)
$ocrBmp = New-Object System.Drawing.Bitmap($ocrW, $ocrH)
$ocrG = [System.Drawing.Graphics]::FromImage($ocrBmp)
$ocrG.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$ocrG.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$ocrG.DrawImage($bmp, 0, 0, $ocrW, $ocrH)
$ocrG.Dispose()
for ($py = 0; $py -lt $ocrH; $py++) {{
  for ($px = 0; $px -lt $ocrW; $px++) {{
    $c = $ocrBmp.GetPixel($px, $py)
    $gray = [int](($c.R * 0.299) + ($c.G * 0.587) + ($c.B * 0.114))
    if ($threshold -gt 0) {{
      if ($gray -ge $threshold) {{ $gray = 255 }} else {{ $gray = 0 }}
    }}
    $ocrBmp.SetPixel($px, $py, [System.Drawing.Color]::FromArgb($gray, $gray, $gray))
  }}
}}
$ocrStream = [System.IO.File]::Open({str(ocr_output_path)!r}, [System.IO.FileMode]::Create)
$ocrBmp.Save($ocrStream, [System.Drawing.Imaging.ImageFormat]::Png)
$ocrStream.Dispose()
$ocrBmp.Dispose()
$bmp.Dispose()
Write-Output "$winW,$winH,$x,$y,$w,$h,process,$targetPid,$fgPid,$focusAttempted,$focusSucceeded,$avgGray,$nearBlackPercent"
"""
    completed = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=18,
    )
    if completed.returncode != 0:
        raise RuntimeError(_safe_text(completed.stderr).strip() or _safe_text(completed.stdout).strip() or "process mainwindow capture failed")
    stdout_text = _safe_text(completed.stdout).strip()
    parts = (stdout_text.splitlines()[-1] if stdout_text else "").split(",")
    return {
        "name": region["name"],
        "percent": region,
        "window_width": int(parts[0]) if len(parts) >= 6 else None,
        "window_height": int(parts[1]) if len(parts) >= 6 else None,
        "x": int(parts[2]) if len(parts) >= 6 else None,
        "y": int(parts[3]) if len(parts) >= 6 else None,
        "w": int(parts[4]) if len(parts) >= 6 else None,
        "h": int(parts[5]) if len(parts) >= 6 else None,
        "window_match_mode": parts[6] if len(parts) >= 7 else "process",
        "process_id": int(parts[7]) if len(parts) >= 8 and parts[7].isdigit() else None,
        "foreground_process_id": int(parts[8]) if len(parts) >= 9 and parts[8].isdigit() else None,
        "focus_attempted": parts[9].lower() == "true" if len(parts) >= 10 else False,
        "focus_succeeded": parts[10].lower() == "true" if len(parts) >= 11 else False,
        "average_gray": float(parts[11]) if len(parts) >= 12 else None,
        "near_black_percent": float(parts[12]) if len(parts) >= 13 else None,
        "dpi_aware": True,
    }


def _is_black_capture_payload(payload: dict[str, Any]) -> bool:
    avg_gray = payload.get("average_gray")
    near_black = payload.get("near_black_percent")
    if avg_gray is None or near_black is None:
        return False
    return float(avg_gray) <= 3.0 and float(near_black) >= 98.0


def _capture_region_with_legacy_gdi(
    *,
    window_title: str,
    process_name: str,
    region: dict[str, Any],
    output_path: Path,
    ocr_output_path: Path,
    scale: int,
    threshold: int,
) -> dict[str, Any]:
    script = rf"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32LegacyGdi {{
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out int processId);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
}}
public struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}
"@
$needle = {window_title!r}
$targetProcess = {process_name!r}
$candidates = @()
$callback = [Win32LegacyGdi+EnumWindowsProc] {{
  param([IntPtr]$hWnd, [IntPtr]$lParam)
  if (-not [Win32LegacyGdi]::IsWindowVisible($hWnd)) {{ return $true }}
  $sb = New-Object System.Text.StringBuilder 512
  [void][Win32LegacyGdi]::GetWindowText($hWnd, $sb, $sb.Capacity)
  $title = $sb.ToString()
  $procId = 0
  [void][Win32LegacyGdi]::GetWindowThreadProcessId($hWnd, [ref]$procId)
  $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
  $procExe = if ($proc) {{ $proc.ProcessName + '.exe' }} else {{ '' }}
  $processMatch = ($procExe -ieq $targetProcess)
  $titleMatch = ($title -like "*$needle*")
  if ($processMatch -or $titleMatch) {{
      $rectTry = New-Object RECT
      [void][Win32LegacyGdi]::GetWindowRect($hWnd, [ref]$rectTry)
      $wTry = $rectTry.Right - $rectTry.Left
      $hTry = $rectTry.Bottom - $rectTry.Top
      if ($wTry -gt 32 -and $hTry -gt 32) {{
        $priority = if ($processMatch) {{ 2 }} elseif ($titleMatch) {{ 1 }} else {{ 0 }}
        $candidates += [pscustomobject]@{{ Hwnd=$hWnd; Left=$rectTry.Left; Top=$rectTry.Top; W=$wTry; H=$hTry; Area=($wTry*$hTry); Priority=$priority; Pid=$procId; Title=$title }}
      }}
  }}
  return $true
}}
[void][Win32LegacyGdi]::EnumWindows($callback, [IntPtr]::Zero)
if ($candidates.Count -eq 0) {{ throw "Window not found: $needle" }}
$chosen = $candidates | Sort-Object Priority, Area -Descending | Select-Object -First 1
$winW = [int]$chosen.W
$winH = [int]$chosen.H
$left = [int]$chosen.Left
$top = [int]$chosen.Top
$x = [Math]::Floor($winW * {region['x']} / 100)
$y = [Math]::Floor($winH * {region['y']} / 100)
$w = [Math]::Floor($winW * {region['w']} / 100)
$h = [Math]::Floor($winH * {region['h']} / 100)
$w = [Math]::Max(1, [int]$w)
$h = [Math]::Max(1, [int]$h)
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($left + $x, $top + $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$g.Dispose()
$sampleStepX = [Math]::Max(1, [Math]::Floor($w / 80))
$sampleStepY = [Math]::Max(1, [Math]::Floor($h / 45))
$sampleCount = 0
$graySum = 0
$nearBlackCount = 0
for ($sampleY = 0; $sampleY -lt $h; $sampleY += $sampleStepY) {{
  for ($sampleX = 0; $sampleX -lt $w; $sampleX += $sampleStepX) {{
    $sampleColor = $bmp.GetPixel($sampleX, $sampleY)
    $sampleGray = [int](($sampleColor.R * 0.299) + ($sampleColor.G * 0.587) + ($sampleColor.B * 0.114))
    $graySum += $sampleGray
    $sampleCount += 1
    if ($sampleGray -lt 8) {{ $nearBlackCount += 1 }}
  }}
}}
$avgGray = 0
$nearBlackPercent = 0
if ($sampleCount -gt 0) {{
  $avgGray = [Math]::Round($graySum / $sampleCount, 2)
  $nearBlackPercent = [Math]::Round(100 * $nearBlackCount / $sampleCount, 2)
}}
$rawStream = [System.IO.File]::Open({str(output_path)!r}, [System.IO.FileMode]::Create)
$bmp.Save($rawStream, [System.Drawing.Imaging.ImageFormat]::Png)
$rawStream.Dispose()
$scale = [Math]::Max(1, {scale})
$threshold = [Math]::Max(0, [Math]::Min(255, {threshold}))
if ($scale -eq 1 -and $threshold -eq 0) {{
  $ocrStream = [System.IO.File]::Open({str(ocr_output_path)!r}, [System.IO.FileMode]::Create)
  $bmp.Save($ocrStream, [System.Drawing.Imaging.ImageFormat]::Png)
  $ocrStream.Dispose()
  $bmp.Dispose()
  $rawLen = 0
  $ocrLen = 0
  if (Test-Path -LiteralPath {str(output_path)!r}) {{ $rawLen = (Get-Item -LiteralPath {str(output_path)!r}).Length }}
  if (Test-Path -LiteralPath {str(ocr_output_path)!r}) {{ $ocrLen = (Get-Item -LiteralPath {str(ocr_output_path)!r}).Length }}
  Write-Output "$winW,$winH,$x,$y,$w,$h,title,$($chosen.Pid),0,False,False,$avgGray,$nearBlackPercent,$rawLen,$ocrLen"
  return
}}
$ocrW = [Math]::Max(1, $w * $scale)
$ocrH = [Math]::Max(1, $h * $scale)
$ocrBmp = New-Object System.Drawing.Bitmap($ocrW, $ocrH)
$ocrG = [System.Drawing.Graphics]::FromImage($ocrBmp)
$ocrG.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$ocrG.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$ocrG.DrawImage($bmp, 0, 0, $ocrW, $ocrH)
$ocrG.Dispose()
for ($py = 0; $py -lt $ocrH; $py++) {{
  for ($px = 0; $px -lt $ocrW; $px++) {{
    $c = $ocrBmp.GetPixel($px, $py)
    $gray = [int](($c.R * 0.299) + ($c.G * 0.587) + ($c.B * 0.114))
    if ($threshold -gt 0) {{
      if ($gray -ge $threshold) {{ $gray = 255 }} else {{ $gray = 0 }}
    }}
    $ocrBmp.SetPixel($px, $py, [System.Drawing.Color]::FromArgb($gray, $gray, $gray))
  }}
}}
$ocrStream = [System.IO.File]::Open({str(ocr_output_path)!r}, [System.IO.FileMode]::Create)
$ocrBmp.Save($ocrStream, [System.Drawing.Imaging.ImageFormat]::Png)
$ocrStream.Dispose()
$ocrBmp.Dispose()
$bmp.Dispose()
$rawLen = 0
$ocrLen = 0
if (Test-Path -LiteralPath {str(output_path)!r}) {{ $rawLen = (Get-Item -LiteralPath {str(output_path)!r}).Length }}
if (Test-Path -LiteralPath {str(ocr_output_path)!r}) {{ $ocrLen = (Get-Item -LiteralPath {str(ocr_output_path)!r}).Length }}
Write-Output "$winW,$winH,$x,$y,$w,$h,title,$($chosen.Pid),0,False,False,$avgGray,$nearBlackPercent,$rawLen,$ocrLen"
"""
    completed = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise RuntimeError(_safe_text(completed.stderr).strip() or _safe_text(completed.stdout).strip() or "legacy capture failed")
    stdout_text = _safe_text(completed.stdout).strip()
    parts = (stdout_text.splitlines()[-1] if stdout_text else "").split(",")
    return {
        "name": region["name"],
        "percent": region,
        "window_width": int(parts[0]) if len(parts) >= 6 else None,
        "window_height": int(parts[1]) if len(parts) >= 6 else None,
        "x": int(parts[2]) if len(parts) >= 6 else None,
        "y": int(parts[3]) if len(parts) >= 6 else None,
        "w": int(parts[4]) if len(parts) >= 6 else None,
        "h": int(parts[5]) if len(parts) >= 6 else None,
        "window_match_mode": parts[6] if len(parts) >= 7 else "title",
        "process_id": None,
        "foreground_process_id": None,
        "focus_attempted": False,
        "focus_succeeded": False,
        "average_gray": float(parts[11]) if len(parts) >= 12 else None,
        "near_black_percent": float(parts[12]) if len(parts) >= 13 else None,
        "raw_file_bytes": int(parts[13]) if len(parts) >= 14 and parts[13].isdigit() else None,
        "ocr_file_bytes": int(parts[14]) if len(parts) >= 15 and parts[14].isdigit() else None,
        "dpi_aware": False,
    }


def _capture_region_with_powershell(
    *,
    window_title: str,
    process_name: str,
    auto_focus: bool,
    region: dict[str, Any],
    output_path: Path,
    ocr_output_path: Path,
    scale: int,
    threshold: int,
    dpi_aware: bool,
    require_foreground: bool,
) -> dict[str, Any]:
    process_exe, process_base = _normalize_process_identity(process_name)
    script = rf"""
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {{
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr SetActiveWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
  [DllImport("user32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
  [DllImport("user32.dll")] public static extern bool AllowSetForegroundWindow(int dwProcessId);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
}}
public struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}
"@
{"[void][Win32]::SetProcessDPIAware()" if dpi_aware else ""}
$needle = {window_title!r}
$targetProcessName = {process_name!r}
$targetProcessExe = {process_exe!r}
$targetProcessBase = {process_base!r}
$autoFocus = {"$true" if auto_focus else "$false"}
$requireForeground = {"$true" if require_foreground else "$false"}
$targetPid = 0
$focusAttempted = $false
$focusSucceeded = $false
if ($targetProcessName) {{
  $process = Get-Process -ErrorAction SilentlyContinue |
    Where-Object {{
      $_.MainWindowHandle -ne 0 -and (
        $_.ProcessName -ieq $targetProcessBase -or
        ($_.Path -and [System.IO.Path]::GetFileName($_.Path) -ieq $targetProcessExe)
      )
    }} |
    Select-Object -First 1
  if ($process) {{ $targetPid = [uint32]$process.Id }}
}}
$found = [IntPtr]::Zero
$foundMode = ""
$callback = [Win32+EnumWindowsProc] {{
  param([IntPtr]$hWnd, [IntPtr]$lParam)
  if (-not [Win32]::IsWindowVisible($hWnd)) {{ return $true }}
  if ($targetPid -ne 0) {{
    [uint32]$windowProcessId = 0
    [void][Win32]::GetWindowThreadProcessId($hWnd, [ref]$windowProcessId)
    if ($windowProcessId -eq $targetPid) {{
      $script:found = $hWnd
      $script:foundMode = "process"
      return $false
    }}
    return $true
  }}
  $sb = New-Object System.Text.StringBuilder 512
  [void][Win32]::GetWindowText($hWnd, $sb, $sb.Capacity)
  $title = $sb.ToString()
  if ($title -like "*$needle*") {{
    $script:found = $hWnd
    $script:foundMode = "title"
    return $false
  }}
  return $true
}}
[void][Win32]::EnumWindows($callback, [IntPtr]::Zero)
if ($found -eq [IntPtr]::Zero) {{
  if ($targetProcessName) {{ throw "Window not found: process=$targetProcessName title=$needle window_match_mode=none" }}
  throw "Window not found: $needle"
}}
if ([Win32]::IsIconic($found)) {{
  if ($autoFocus) {{
    $focusAttempted = $true
    [void][Win32]::ShowWindow($found, 9)
    Start-Sleep -Milliseconds 250
  }} else {{
    throw "DOTA_MINIMIZED: process=$targetProcessName pid=$targetPid window_match_mode=$foundMode auto_focus=$autoFocus focus_succeeded=$focusSucceeded"
  }}
}}
if ($autoFocus) {{
  $focusAttempted = $true
  [void][Win32]::ShowWindow($found, 9)
  [void][Win32]::ShowWindow($found, 5)
  try {{
    $shell = New-Object -ComObject WScript.Shell
    [void]$shell.SendKeys('%')
    Start-Sleep -Milliseconds 80
    [void]$shell.AppActivate([int]$targetPid)
  }} catch {{}}
  [void][Win32]::AllowSetForegroundWindow([int]$targetPid)
  $foregroundBefore = [Win32]::GetForegroundWindow()
  [uint32]$foregroundThreadId = 0
  if ($foregroundBefore -ne [IntPtr]::Zero) {{
    [void][Win32]::GetWindowThreadProcessId($foregroundBefore, [ref]$foregroundThreadId)
  }}
  [uint32]$targetThreadPid = 0
  $targetThreadId = [Win32]::GetWindowThreadProcessId($found, [ref]$targetThreadPid)
  $currentThreadId = [Win32]::GetCurrentThreadId()
  if ($foregroundThreadId -ne 0) {{
    [void][Win32]::AttachThreadInput($currentThreadId, $foregroundThreadId, $true)
  }}
  if ($targetThreadId -ne 0) {{
    [void][Win32]::AttachThreadInput($currentThreadId, $targetThreadId, $true)
  }}
  [void][Win32]::BringWindowToTop($found)
  [void][Win32]::SetActiveWindow($found)
  [void][Win32]::SetFocus($found)
  [void][Win32]::SetForegroundWindow($found)
  if ($targetThreadId -ne 0) {{
    [void][Win32]::AttachThreadInput($currentThreadId, $targetThreadId, $false)
  }}
  if ($foregroundThreadId -ne 0) {{
    [void][Win32]::AttachThreadInput($currentThreadId, $foregroundThreadId, $false)
  }}
  Start-Sleep -Milliseconds 250
  for ($attempt = 0; $attempt -lt 3; $attempt++) {{
    $currentForeground = [Win32]::GetForegroundWindow()
    [uint32]$currentForegroundPid = 0
    if ($currentForeground -ne [IntPtr]::Zero) {{
      [void][Win32]::GetWindowThreadProcessId($currentForeground, [ref]$currentForegroundPid)
    }}
    if ($currentForeground -eq $found -or ($targetPid -ne 0 -and $currentForegroundPid -eq $targetPid)) {{
      break
    }}
    [void][Win32]::ShowWindow($found, 5)
    [void][Win32]::BringWindowToTop($found)
    [void][Win32]::SetForegroundWindow($found)
    Start-Sleep -Milliseconds 180
  }}
}}
$foreground = [Win32]::GetForegroundWindow()
[uint32]$foregroundPid = 0
if ($foreground -ne [IntPtr]::Zero) {{
  [void][Win32]::GetWindowThreadProcessId($foreground, [ref]$foregroundPid)
}}
$focusSucceeded = ($foreground -eq $found)
if ($targetPid -ne 0) {{
  $focusSucceeded = $focusSucceeded -or ($foregroundPid -eq $targetPid)
}}
if ($requireForeground -and -not $focusSucceeded) {{
  throw "DOTA_NOT_FOREGROUND: process=$targetProcessName pid=$targetPid foreground_pid=$foregroundPid window_match_mode=$foundMode auto_focus=$focusAttempted focus_succeeded=$focusSucceeded"
}}
$rect = New-Object RECT
[void][Win32]::GetWindowRect($found, [ref]$rect)
$winW = $rect.Right - $rect.Left
$winH = $rect.Bottom - $rect.Top
if ($winW -lt 640 -or $winH -lt 360) {{
  throw "DOTA_WINDOW_TOO_SMALL: process=$targetProcessName pid=$targetPid window_match_mode=$foundMode width=$winW height=$winH auto_focus=$focusAttempted focus_succeeded=$focusSucceeded"
}}
$x = [Math]::Floor($winW * {region['x']} / 100)
$y = [Math]::Floor($winH * {region['y']} / 100)
$w = [Math]::Floor($winW * {region['w']} / 100)
$h = [Math]::Floor($winH * {region['h']} / 100)
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($rect.Left + $x, $rect.Top + $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$g.Dispose()
$sampleStepX = [Math]::Max(1, [Math]::Floor($w / 80))
$sampleStepY = [Math]::Max(1, [Math]::Floor($h / 45))
$sampleCount = 0
$graySum = 0
$nearBlackCount = 0
for ($sampleY = 0; $sampleY -lt $h; $sampleY += $sampleStepY) {{
  for ($sampleX = 0; $sampleX -lt $w; $sampleX += $sampleStepX) {{
    $sampleColor = $bmp.GetPixel($sampleX, $sampleY)
    $sampleGray = [int](($sampleColor.R * 0.299) + ($sampleColor.G * 0.587) + ($sampleColor.B * 0.114))
    $graySum += $sampleGray
    $sampleCount += 1
    if ($sampleGray -lt 8) {{ $nearBlackCount += 1 }}
  }}
}}
$avgGray = 0
$nearBlackPercent = 0
if ($sampleCount -gt 0) {{
  $avgGray = [Math]::Round($graySum / $sampleCount, 2)
  $nearBlackPercent = [Math]::Round(100 * $nearBlackCount / $sampleCount, 2)
}}
$rawBmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$rawG = [System.Drawing.Graphics]::FromImage($rawBmp)
$rawG.DrawImage($bmp, 0, 0, $w, $h)
$rawG.Dispose()
$rawStream = [System.IO.File]::Open({str(output_path)!r}, [System.IO.FileMode]::Create)
$rawBmp.Save($rawStream, [System.Drawing.Imaging.ImageFormat]::Png)
$rawStream.Dispose()
$rawBmp.Dispose()
$scale = [Math]::Max(1, {scale})
$threshold = [Math]::Max(0, [Math]::Min(255, {threshold}))
if ($nearBlackPercent -ge 98 -and $avgGray -le 3) {{
  $ocrStream = [System.IO.File]::Open({str(ocr_output_path)!r}, [System.IO.FileMode]::Create)
  $bmp.Save($ocrStream, [System.Drawing.Imaging.ImageFormat]::Png)
  $ocrStream.Dispose()
  $bmp.Dispose()
  Write-Output "$winW,$winH,$x,$y,$w,$h,$foundMode,$targetPid,$foregroundPid,$focusAttempted,$focusSucceeded,$avgGray,$nearBlackPercent"
  return
}}
if ($scale -eq 1 -and $threshold -eq 0) {{
  $ocrStream = [System.IO.File]::Open({str(ocr_output_path)!r}, [System.IO.FileMode]::Create)
  $bmp.Save($ocrStream, [System.Drawing.Imaging.ImageFormat]::Png)
  $ocrStream.Dispose()
  $bmp.Dispose()
  Write-Output "$winW,$winH,$x,$y,$w,$h,$foundMode,$targetPid,$foregroundPid,$focusAttempted,$focusSucceeded,$avgGray,$nearBlackPercent"
  return
}}
$ocrW = [Math]::Max(1, $w * $scale)
$ocrH = [Math]::Max(1, $h * $scale)
$ocrBmp = New-Object System.Drawing.Bitmap($ocrW, $ocrH)
$ocrG = [System.Drawing.Graphics]::FromImage($ocrBmp)
$ocrG.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$ocrG.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$ocrG.DrawImage($bmp, 0, 0, $ocrW, $ocrH)
$ocrG.Dispose()
for ($py = 0; $py -lt $ocrH; $py++) {{
  for ($px = 0; $px -lt $ocrW; $px++) {{
    $c = $ocrBmp.GetPixel($px, $py)
    $gray = [int](($c.R * 0.299) + ($c.G * 0.587) + ($c.B * 0.114))
    if ($threshold -gt 0) {{
      if ($gray -ge $threshold) {{ $gray = 255 }} else {{ $gray = 0 }}
    }}
    $ocrBmp.SetPixel($px, $py, [System.Drawing.Color]::FromArgb($gray, $gray, $gray))
  }}
}}
$ocrStream = [System.IO.File]::Open({str(ocr_output_path)!r}, [System.IO.FileMode]::Create)
$ocrBmp.Save($ocrStream, [System.Drawing.Imaging.ImageFormat]::Png)
$ocrStream.Dispose()
$ocrBmp.Dispose()
$bmp.Dispose()
Write-Output "$winW,$winH,$x,$y,$w,$h,$foundMode,$targetPid,$foregroundPid,$focusAttempted,$focusSucceeded,$avgGray,$nearBlackPercent"
"""
    completed = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(_safe_text(completed.stderr).strip() or _safe_text(completed.stdout).strip() or "capture failed")
    stdout_text = _safe_text(completed.stdout).strip()
    parts = (stdout_text.splitlines()[-1] if stdout_text else "").split(",")
    return {
        "name": region["name"],
        "percent": region,
        "window_width": int(parts[0]) if len(parts) >= 6 else None,
        "window_height": int(parts[1]) if len(parts) >= 6 else None,
        "x": int(parts[2]) if len(parts) >= 6 else None,
        "y": int(parts[3]) if len(parts) >= 6 else None,
        "w": int(parts[4]) if len(parts) >= 6 else None,
        "h": int(parts[5]) if len(parts) >= 6 else None,
        "window_match_mode": parts[6] if len(parts) >= 7 else None,
        "process_id": int(parts[7]) if len(parts) >= 8 and parts[7].isdigit() else None,
        "foreground_process_id": int(parts[8]) if len(parts) >= 9 and parts[8].isdigit() else None,
        "focus_attempted": parts[9].lower() == "true" if len(parts) >= 10 else False,
        "focus_succeeded": parts[10].lower() == "true" if len(parts) >= 11 else False,
        "average_gray": float(parts[11]) if len(parts) >= 12 else None,
        "near_black_percent": float(parts[12]) if len(parts) >= 13 else None,
        "dpi_aware": dpi_aware,
        "capture_backend_variant": "gdi_dpi_aware" if dpi_aware else "gdi_legacy_virtual",
    }
