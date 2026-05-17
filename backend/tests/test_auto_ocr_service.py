from __future__ import annotations

import threading
from pathlib import Path

from services import auto_ocr_service


def test_powershell_executable_returns_a_string():
    result = auto_ocr_service._powershell_executable()

    assert result
    assert result.endswith("powershell.exe") or result == "powershell"


def test_resolve_tesseract_prefers_backend_bundled_path(tmp_path, monkeypatch):
    backend_dir = tmp_path / "backend"
    bundled = backend_dir / "tools" / "tesseract" / "tesseract.exe"
    configured = tmp_path / "configured" / "tesseract.exe"
    bundled.parent.mkdir(parents=True)
    configured.parent.mkdir(parents=True)
    bundled.write_text("", encoding="utf-8")
    configured.write_text("", encoding="utf-8")

    monkeypatch.setattr(auto_ocr_service, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(auto_ocr_service.shutil, "which", lambda _: None)

    result = auto_ocr_service._resolve_tesseract_path(str(configured))

    assert result["path"] == str(bundled)
    assert result["source"] == "bundled"


def test_resolve_tesseract_uses_configured_path_when_no_bundle(tmp_path, monkeypatch):
    backend_dir = tmp_path / "backend"
    configured = tmp_path / "configured" / "tesseract.exe"
    configured.parent.mkdir(parents=True)
    configured.write_text("", encoding="utf-8")

    monkeypatch.setattr(auto_ocr_service, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(auto_ocr_service.shutil, "which", lambda _: None)

    result = auto_ocr_service._resolve_tesseract_path(str(configured))

    assert result["path"] == str(configured)
    assert result["source"] == "configured"


def test_resolve_tesseract_uses_path_lookup(tmp_path, monkeypatch):
    backend_dir = tmp_path / "backend"
    path_tesseract = tmp_path / "path" / "tesseract.exe"

    monkeypatch.setattr(auto_ocr_service, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(auto_ocr_service.shutil, "which", lambda _: str(path_tesseract))

    result = auto_ocr_service._resolve_tesseract_path("tesseract")

    assert result["path"] == str(path_tesseract)
    assert result["source"] == "path"


def test_resolve_tesseract_reports_missing_with_checked_paths(tmp_path, monkeypatch):
    backend_dir = tmp_path / "backend"

    monkeypatch.setattr(auto_ocr_service, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(auto_ocr_service.shutil, "which", lambda _: None)
    monkeypatch.setattr(auto_ocr_service.Path, "exists", lambda self: False)

    result = auto_ocr_service._resolve_tesseract_path("tesseract")

    assert result["path"] == "tesseract"
    assert result["source"] == "missing"
    checked = [Path(item).name for item in result["checked_paths"]]
    assert "tesseract.exe" in checked
    assert "tesseract" in checked


def test_bounded_int_keeps_zero_when_zero_is_valid():
    assert auto_ocr_service._bounded_int(0, default=7, minimum=0, maximum=13) == 0
    assert auto_ocr_service._bounded_int("0", default=175, minimum=0, maximum=255) == 0
    assert auto_ocr_service._bounded_int(999, default=7, minimum=0, maximum=13) == 13


def test_build_auto_ocr_regions_includes_groups_and_ten_slots():
    regions = auto_ocr_service._build_auto_ocr_regions(include_slots=True)

    assert [region["name"] for region in regions[:2]] == ["left5", "right5"]
    slots = [region for region in regions if region["kind"] == "slot"]
    assert len(slots) == 10
    assert [slot["slot_name"] for slot in slots[:5]] == ["radiant1", "radiant2", "radiant3", "radiant4", "radiant5"]
    assert [slot["slot_name"] for slot in slots[5:]] == ["dire1", "dire2", "dire3", "dire4", "dire5"]
    assert slots[0]["x"] > 10
    assert slots[4]["x"] + slots[4]["w"] < 43
    assert slots[5]["x"] > 57
    assert slots[9]["x"] + slots[9]["w"] < 90


def test_build_auto_ocr_regions_without_slots():
    regions = auto_ocr_service._build_auto_ocr_regions(include_slots=False)

    assert [region["name"] for region in regions] == ["left5", "right5"]


def test_build_slot_regions_stay_in_bounds_and_non_overlapping():
    slots = auto_ocr_service._build_slot_regions(team="radiant", start_x=10, y=8, total_w=33, h=3)

    assert len(slots) == 5
    assert [slot["slot_index"] for slot in slots] == [1, 2, 3, 4, 5]
    assert all(slot["slot_name"] == f"radiant{slot['slot_index']}" for slot in slots)

    left_bound = 10
    right_bound = 43
    for slot in slots:
        assert slot["x"] >= left_bound
        assert slot["x"] + slot["w"] <= right_bound

    for left, right in zip(slots, slots[1:]):
        assert left["x"] + left["w"] < right["x"]


def test_build_hit_key_deduplicates_and_sorts_player_ids():
    result = auto_ocr_service._build_hit_key(
        {
            "matches": [
                {"matched_player_id": "42"},
                {"matched_player_id": "7"},
                {"matched_player_id": "42"},
                {"matched_player_id": ""},
            ]
        }
    )

    assert result == "42|7"


def test_empty_confirmation_uses_required_scans():
    result = auto_ocr_service._empty_confirmation(3)

    assert result["required_scans"] == 3
    assert result["stable_hit_count"] == 0
    assert result["should_publish"] is False


def test_find_black_capture_detects_near_black_frames():
    result = auto_ocr_service._find_black_capture(
        [
            {"name": "full-window", "average_gray": 0.0, "near_black_percent": 100.0, "path": "full.png"},
        ]
    )

    assert result["name"] == "full-window"
    assert result["path"] == "full.png"


def test_find_black_capture_ignores_normal_frames():
    result = auto_ocr_service._find_black_capture(
        [
            {"name": "full-window", "average_gray": 45.0, "near_black_percent": 12.0},
        ]
    )

    assert result is None


def test_is_black_capture_payload():
    assert auto_ocr_service._is_black_capture_payload(
        {"average_gray": 0.0, "near_black_percent": 100.0}
    )
    assert not auto_ocr_service._is_black_capture_payload(
        {"average_gray": 38.0, "near_black_percent": 0.0}
    )


def test_probe_full_window_uses_legacy_capture_without_ocr(tmp_path, monkeypatch):
    service = auto_ocr_service.AutoOcrService.__new__(auto_ocr_service.AutoOcrService)
    service.output_dir = tmp_path
    service.window_title = "Dota 2"
    service.process_name = "dota2.exe"
    service._thread = None
    service._lock = threading.Lock()

    class RuntimeStatus:
        def __init__(self):
            self.payload = None

        def merge(self, payload):
            self.payload = payload

    service.runtime_status = RuntimeStatus()

    def fake_capture(**kwargs):
        assert kwargs["region"]["name"] == "full-window"
        assert kwargs["scale"] == 1
        assert kwargs["threshold"] == 0
        return {
            "name": "full-window",
            "window_width": 2560,
            "window_height": 1440,
            "average_gray": 38.0,
            "near_black_percent": 0.0,
        }

    monkeypatch.setattr(auto_ocr_service, "_capture_region_with_legacy_gdi", fake_capture)

    result = service.probe_full_window()

    assert result["state"] == "captured"
    assert result["capture_backend_variant"] == "gdi_legacy_title"
    assert result["window_width"] == 2560
    assert result["path"].endswith("probe\\full-window.png") or result["path"].endswith("probe/full-window.png")
    assert service.runtime_status.payload["auto_ocr_probe"]["state"] == "captured"


def test_update_confirmation_requires_stable_repeated_hit():
    service = auto_ocr_service.AutoOcrService.__new__(auto_ocr_service.AutoOcrService)
    service.confirm_scans = 2
    service.cooldown_seconds = 0
    service._stable_hit_key = None
    service._stable_hit_count = 0
    service._last_published_hits = {}
    match_result = {"matches": [{"matched_player_id": "42"}]}

    first = service._update_confirmation(match_result)
    second = service._update_confirmation(match_result)

    assert first["stable_hit_count"] == 1
    assert first["confirmed"] is False
    assert first["should_publish"] is False
    assert second["stable_hit_count"] == 2
    assert second["confirmed"] is True
    assert second["should_publish"] is True


def test_update_confirmation_applies_cooldown_for_same_hit_key():
    service = auto_ocr_service.AutoOcrService.__new__(auto_ocr_service.AutoOcrService)
    service.confirm_scans = 1
    service.cooldown_seconds = 180
    service._stable_hit_key = None
    service._stable_hit_count = 0
    service._last_published_hits = {}
    match_result = {"matches": [{"matched_player_id": "42"}]}

    first = service._update_confirmation(match_result)
    second = service._update_confirmation(match_result)

    assert first["should_publish"] is True
    assert second["confirmed"] is True
    assert second["should_publish"] is False
    assert second["cooldown_remaining_seconds"] > 0


def test_run_once_publishes_alerts_only_after_confirmation(tmp_path, monkeypatch):
    service = auto_ocr_service.AutoOcrService.__new__(auto_ocr_service.AutoOcrService)
    service.output_dir = tmp_path
    service.window_title = "Dota 2"
    service.process_name = "dota2.exe"
    service.auto_focus = False
    service.use_slots = False
    service.debug_images = False
    service.debug_dir = tmp_path / "debug"
    service.debug_max_runs = 80
    service.interval_seconds = 6
    service.configured_tesseract_path = "tesseract"
    service.tesseract_path = "tesseract"
    service.tesseract_source = "test"
    service.tesseract_checked_paths = []
    service.requested_tesseract_lang = "eng"
    service.tesseract_lang = "eng"
    service.tesseract_missing_langs = []
    service.tesseract_tessdata_dir = ""
    service.tesseract_psm = 7
    service.tesseract_oem = 1
    service.image_scale = 4
    service.image_threshold = 175
    service.threshold = 0.72
    service.min_encounters = 2
    service.confirm_scans = 2
    service.cooldown_seconds = 0
    service.require_tagged = False
    service.default_account_id = "126600075"
    service._stop = threading.Event()
    service._thread = None
    service._lock = threading.Lock()
    service._last_status = {}
    service._last_capture_status = {}
    service._stable_hit_key = None
    service._stable_hit_count = 0
    service._last_published_hits = {}

    class RuntimeStatus:
        def __init__(self):
            self.payloads = []

        def merge(self, payload):
            self.payloads.append(payload)

    class OcrMatcher:
        def match_text(self, **kwargs):
            return {
                "source": "ocr_text_match",
                "match_count": 1,
                "matches": [{"matched_player_id": "42", "matched_name": "KnownPlayer"}],
            }

    class AlertsWriter:
        def __init__(self):
            self.writes = []

        def write(self, payload):
            self.writes.append(payload)

    class EventsRepo:
        def __init__(self):
            self.events = []

        def append_event(self, event_type, payload):
            self.events.append((event_type, payload))

    service.runtime_status = RuntimeStatus()
    service.ocr_match_service = OcrMatcher()
    service.alerts_writer = AlertsWriter()
    service.events_repo = EventsRepo()
    service.alert_payload_builder = lambda match_result: {
        "hit_count": match_result["match_count"],
        "items": match_result["matches"],
    }

    monkeypatch.setattr(
        service,
        "_capture_regions",
        lambda: [{"name": "left5", "kind": "group", "path": str(tmp_path / "left5.png")}],
    )
    monkeypatch.setattr(service, "_archive_test_images", lambda **kwargs: tmp_path)
    monkeypatch.setattr(service, "_tesseract_available", lambda: True)
    monkeypatch.setattr(service, "_ocr_capture_text", lambda capture: "KnownPlayer")

    first = service.run_once(publish_alerts=True)
    second = service.run_once(publish_alerts=True)

    assert first["confirmation"]["confirmed"] is False
    assert first["confirmation"]["should_publish"] is False
    assert second["confirmation"]["confirmed"] is True
    assert second["confirmation"]["should_publish"] is True
    assert len(service.alerts_writer.writes) == 1
    assert service.alerts_writer.writes[0]["confirmation"]["confirmed"] is True
    published_events = [
        event for event in service.events_repo.events if event[0] == "auto_ocr_alerts_published"
    ]
    assert len(published_events) == 1


def test_resolve_tesseract_lang_drops_missing_languages(tmp_path):
    tesseract = tmp_path / "tesseract.exe"
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    tesseract.write_text("", encoding="utf-8")
    (tessdata / "eng.traineddata").write_text("", encoding="utf-8")

    result = auto_ocr_service._resolve_tesseract_lang(str(tesseract), "eng+chi_sim")

    assert result["lang"] == "eng"
    assert result["missing_langs"] == ["chi_sim"]


def test_resolve_tesseract_lang_keeps_requested_when_tessdata_unknown(tmp_path):
    tesseract = tmp_path / "tesseract.exe"
    tesseract.write_text("", encoding="utf-8")

    result = auto_ocr_service._resolve_tesseract_lang(str(tesseract), "eng+chi_sim")

    assert result["lang"] == "eng+chi_sim"
    assert result["missing_langs"] == []


def test_capture_error_details_for_not_foreground():
    result = auto_ocr_service._capture_error_details(
        "DOTA_NOT_FOREGROUND: process=dota2.exe pid=27516 foreground_pid=35100 window_match_mode=process auto_focus=True focus_succeeded=False"
    )

    assert result["state"] == "dota_not_foreground"
    assert result["focus_attempted"] is True
    assert result["focus_succeeded"] is False
    assert result["process_id"] == 27516
    assert result["foreground_process_id"] == 35100
    assert result["window_match_mode"] == "process"


def test_capture_error_details_for_minimized():
    result = auto_ocr_service._capture_error_details(
        "DOTA_MINIMIZED: process=dota2.exe pid=27516 window_match_mode=title auto_focus=False focus_succeeded=False"
    )

    assert result["state"] == "dota_minimized"
    assert result["focus_attempted"] is False
    assert result["focus_succeeded"] is False
    assert result["process_id"] == 27516
    assert result["window_match_mode"] == "title"


def test_classify_capture_error_for_too_small_window():
    assert (
        auto_ocr_service._classify_capture_error(
            "DOTA_WINDOW_TOO_SMALL: process=dota2.exe pid=1 width=200 height=100"
        )
        == "dota_window_too_small"
    )


def test_normalize_process_identity_prefers_exe_name():
    assert auto_ocr_service._normalize_process_identity("dota2") == ("dota2.exe", "dota2")
    assert auto_ocr_service._normalize_process_identity("dota2.exe") == ("dota2.exe", "dota2")


def test_cleanup_debug_runs_keeps_newest_directories(tmp_path):
    service = auto_ocr_service.AutoOcrService.__new__(auto_ocr_service.AutoOcrService)
    service.debug_dir = tmp_path
    service.debug_max_runs = 2
    for name in ["20260509-000001-000000", "20260509-000002-000000", "20260509-000003-000000"]:
        (tmp_path / name).mkdir()

    service._cleanup_debug_runs()

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "20260509-000002-000000",
        "20260509-000003-000000",
    ]
