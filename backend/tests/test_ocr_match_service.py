from __future__ import annotations

from services.ocr_match_service import OcrMatchService


class FakePlayersRepository:
    def __init__(self, candidates):
        self.candidates = candidates

    def list_name_match_candidates(self, limit=1000):
        return self.candidates[:limit]


def test_ocr_match_rejects_short_ascii_noise_tokens():
    service = OcrMatchService(
        FakePlayersRepository(
            [
                {
                    "player_id": "1",
                    "latest_name": "VIPEREEEEEEE",
                    "encounter_names": "VIPEREEEEEEE",
                    "encounter_count": 9,
                    "last_seen_at": "2026-05-01T00:00:00+00:00",
                },
                {
                    "player_id": "2",
                    "latest_name": "AP",
                    "encounter_names": "AP",
                    "encounter_count": 9,
                    "last_seen_at": "2026-05-01T00:00:00+00:00",
                },
                {
                    "player_id": "3",
                    "latest_name": "yes!",
                    "encounter_names": "yes!",
                    "encounter_count": 9,
                    "last_seen_at": "2026-05-01T00:00:00+00:00",
                },
            ]
        )
    )

    result = service.match_text(raw_text="AP\nere\nyes\nTT.\nhho", min_encounters=2)

    assert result["extracted_lines"] == []
    assert result["matches"] == []


def test_ocr_match_keeps_real_long_latin_names():
    service = OcrMatchService(
        FakePlayersRepository(
            [
                {
                    "player_id": "42",
                    "latest_name": "blame me=gg",
                    "encounter_names": "blame me=gg",
                    "encounter_count": 4,
                    "last_seen_at": "2026-05-01T00:00:00+00:00",
                }
            ]
        )
    )

    result = service.match_text(raw_text="Dlame me=gg", threshold=0.72, min_encounters=2)

    assert result["match_count"] == 1
    assert result["matches"][0]["matched_player_id"] == "42"


def test_ocr_match_allows_two_character_chinese_names():
    service = OcrMatchService(
        FakePlayersRepository(
            [
                {
                    "player_id": "77",
                    "latest_name": "张三",
                    "encounter_names": "张三",
                    "encounter_count": 3,
                    "last_seen_at": "2026-05-01T00:00:00+00:00",
                }
            ]
        )
    )

    result = service.match_text(raw_text="张三", threshold=0.72, min_encounters=2)

    assert result["match_count"] == 1
    assert result["matches"][0]["matched_player_id"] == "77"
