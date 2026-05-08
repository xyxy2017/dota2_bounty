from __future__ import annotations

import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infra.runtime_status import RuntimeStatusWriter


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
        window_title: str,
        interval_seconds: float,
        threshold: float,
        min_encounters: int,
        require_tagged: bool,
        alert_payload_builder,
    ) -> None:
        self.output_dir = output_dir
        self.runtime_status = runtime_status
        self.ocr_match_service = ocr_match_service
        self.alerts_writer = alerts_writer
        self.events_repo = events_repo
        self.default_account_id = default_account_id
        self.tesseract_path = tesseract_path
        self.window_title = window_title
        self.interval_seconds = max(3.0, float(interval_seconds or 6))
        self.threshold = threshold
        self.min_encounters = min_encounters
        self.require_tagged = require_tagged
        self.alert_payload_builder = alert_payload_builder
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_status: dict[str, Any] = {}

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
        self._write_status({"enabled": False, "state": "stopped"})
        return self.status()

    def status(self) -> dict[str, Any]:
        state = dict(self._last_status)
        state.setdefault("enabled", bool(self._thread and self._thread.is_alive()))
        state.setdefault("interval_seconds", self.interval_seconds)
        state.setdefault("window_title", self.window_title)
        state.setdefault("tesseract_path", self.tesseract_path)
        state.setdefault("output_dir", str(self.output_dir))
        state.setdefault("needs_tesseract", not self._tesseract_available())
        return state

    def run_once(self, *, publish_alerts: bool = True) -> dict[str, Any]:
        with self._lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            try:
                captures = self._capture_regions()
            except Exception as exc:
                status = {
                    "enabled": bool(self._thread and self._thread.is_alive()),
                    "state": "capture_failed",
                    "error": str(exc),
                    "window_title": self.window_title,
                    "interval_seconds": self.interval_seconds,
                    "tesseract_path": self.tesseract_path,
                    "tesseract_available": self._tesseract_available(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._write_status(status)
                return status
            texts = []
            tesseract_available = self._tesseract_available()
            for capture in captures:
                text = ""
                if tesseract_available:
                    text = self._ocr_image(Path(capture["path"]))
                capture["text"] = text
                if text:
                    texts.append(text)

            raw_text = "\n".join(texts).strip()
            match_result = None
            if raw_text:
                match_result = self.ocr_match_service.match_text(
                    raw_text=raw_text,
                    threshold=self.threshold,
                    exclude_player_id=str(self.default_account_id or ""),
                    min_encounters=self.min_encounters,
                    require_tagged=self.require_tagged,
                )
                self.events_repo.append_event(
                    "auto_ocr_roster_matched",
                    {
                        "match_count": match_result["match_count"],
                        "threshold": self.threshold,
                        "min_encounters": self.min_encounters,
                        "require_tagged": self.require_tagged,
                        "published_alerts": bool(publish_alerts and match_result.get("matches")),
                    },
                )
                if publish_alerts and match_result.get("matches"):
                    alerts_payload = self.alert_payload_builder(match_result)
                    alerts_payload["source"] = "auto_ocr"
                    self.alerts_writer.write(alerts_payload)
                    self.events_repo.append_event("auto_ocr_alerts_published", alerts_payload)

            status = {
                "enabled": bool(self._thread and self._thread.is_alive()),
                "state": "captured" if captures else "no_capture",
                "window_title": self.window_title,
                "interval_seconds": self.interval_seconds,
                "tesseract_path": self.tesseract_path,
                "tesseract_available": tesseract_available,
                "captures": captures,
                "raw_text": raw_text,
                "match_result": match_result,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if not tesseract_available:
                status["warning"] = "tesseract.exe not found; configure DOTA2_BOUNTY_TESSERACT_PATH to enable backend OCR."
            self._write_status(status)
            return status

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
        self.runtime_status.merge({"auto_ocr": status})

    def _capture_regions(self) -> list[dict[str, Any]]:
        regions = [
            {"name": "left5", "x": 10, "y": 8, "w": 33, "h": 3},
            {"name": "right5", "x": 57, "y": 8, "w": 33, "h": 3},
        ]
        captures = []
        for region in regions:
            path = self.output_dir / f"{region['name']}.png"
            payload = _capture_region_with_powershell(
                window_title=self.window_title,
                region=region,
                output_path=path,
            )
            payload["path"] = str(path)
            captures.append(payload)
        return captures

    def _ocr_image(self, path: Path) -> str:
        completed = subprocess.run(
            [self.tesseract_path, str(path), "stdout", "-l", "eng+chi_sim", "--psm", "7"],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()

    def _tesseract_available(self) -> bool:
        configured = Path(self.tesseract_path)
        if configured.exists():
            return True
        return shutil.which(self.tesseract_path) is not None


def _capture_region_with_powershell(*, window_title: str, region: dict[str, Any], output_path: Path) -> dict[str, Any]:
    script = rf"""
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {{
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
}}
public struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}
"@
$needle = {window_title!r}
$found = [IntPtr]::Zero
$callback = [Win32+EnumWindowsProc] {{
  param([IntPtr]$hWnd, [IntPtr]$lParam)
  if (-not [Win32]::IsWindowVisible($hWnd)) {{ return $true }}
  $sb = New-Object System.Text.StringBuilder 512
  [void][Win32]::GetWindowText($hWnd, $sb, $sb.Capacity)
  $title = $sb.ToString()
  if ($title -like "*$needle*") {{
    $script:found = $hWnd
    return $false
  }}
  return $true
}}
[void][Win32]::EnumWindows($callback, [IntPtr]::Zero)
if ($found -eq [IntPtr]::Zero) {{ throw "Window not found: $needle" }}
$rect = New-Object RECT
[void][Win32]::GetWindowRect($found, [ref]$rect)
$winW = $rect.Right - $rect.Left
$winH = $rect.Bottom - $rect.Top
$x = [Math]::Floor($winW * {region['x']} / 100)
$y = [Math]::Floor($winH * {region['y']} / 100)
$w = [Math]::Floor($winW * {region['w']} / 100)
$h = [Math]::Floor($winH * {region['h']} / 100)
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($rect.Left + $x, $rect.Top + $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$g.Dispose()
$bmp.Save({str(output_path)!r}, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "$winW,$winH,$x,$y,$w,$h"
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "capture failed")
    parts = (completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "").split(",")
    return {
        "name": region["name"],
        "percent": region,
        "window_width": int(parts[0]) if len(parts) == 6 else None,
        "window_height": int(parts[1]) if len(parts) == 6 else None,
        "x": int(parts[2]) if len(parts) == 6 else None,
        "y": int(parts[3]) if len(parts) == 6 else None,
        "w": int(parts[4]) if len(parts) == 6 else None,
        "h": int(parts[5]) if len(parts) == 6 else None,
    }
