from __future__ import annotations

from services.gsi_id_probe_service import GsiIdProbeService, ProbeConfig


def test_gsi_id_probe_reports_only_local_player_when_no_roster(tmp_path):
    service = GsiIdProbeService(
        ProbeConfig(output_dir=tmp_path, default_account_id="126600075", write_raw_payloads=True)
    )

    status = service.analyze(
        {
            "provider": {"accountid": "126600075", "name": "blame me=gg"},
            "player": {"name": "blame me=gg"},
            "map": {"game_state": "DOTA_GAMERULES_STATE_HERO_SELECTION"},
        }
    )

    assert status["unique_player_ids"] == ["126600075"]
    assert status["non_local_player_ids"] == []
    assert status["has_direct_other_player_ids"] is False
    assert "只识别到本地玩家 ID" in status["conclusion"]
    assert status["raw_payload_path"]


def test_gsi_id_probe_detects_roster_other_player_ids(tmp_path):
    service = GsiIdProbeService(
        ProbeConfig(output_dir=tmp_path, default_account_id="126600075", write_raw_payloads=False)
    )

    status = service.analyze(
        {
            "provider": {"accountid": "126600075"},
            "map": {"game_state": "DOTA_GAMERULES_STATE_STRATEGY_TIME", "matchid": "123"},
            "roster": {
                "players": [
                    {"account_id": 126600075, "name": "me", "team": "radiant"},
                    {"account_id": 42, "name": "other", "team": "dire"},
                ]
            },
        }
    )

    assert status["match_id"] == "123"
    assert status["has_direct_other_player_ids"] is True
    assert status["non_local_player_ids"] == ["42"]
    assert status["roster_player_id_count"] == 2
    assert "发现 1 个非本地玩家 ID" in status["conclusion"]


def test_gsi_id_probe_normalizes_steam64(tmp_path):
    service = GsiIdProbeService(
        ProbeConfig(output_dir=tmp_path, default_account_id="126600075", write_raw_payloads=False)
    )

    status = service.analyze(
        {
            "player": {"steamid": "76561198086865803", "name": "me"},
            "allplayers": {
                "0": {"steamId": "76561198086865803", "name": "me"},
                "1": {"steamId": "76561197960265770", "name": "other"},
            },
        }
    )

    assert "126600075" in status["unique_player_ids"]
    assert "42" in status["non_local_player_ids"]


def test_gsi_id_probe_reset_clears_raw_payloads(tmp_path):
    service = GsiIdProbeService(
        ProbeConfig(output_dir=tmp_path, default_account_id="126600075", write_raw_payloads=True)
    )
    service.analyze({"player": {"accountid": "126600075"}})

    assert service.list_raw_payloads()["items"]

    status = service.reset()

    assert status["observed_payload_count"] == 0
    assert service.list_raw_payloads()["items"] == []
