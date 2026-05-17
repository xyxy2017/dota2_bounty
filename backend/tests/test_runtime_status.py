from __future__ import annotations

from infra.runtime_status import RuntimeStatusWriter


def test_runtime_status_writer_reads_empty_or_corrupt_file_as_empty(tmp_path):
    path = tmp_path / "runtime-status.json"
    writer = RuntimeStatusWriter(path)

    assert writer.read() == {}

    path.write_text("", encoding="utf-8")
    assert writer.read() == {}

    path.write_text("{", encoding="utf-8")
    assert writer.read() == {}


def test_runtime_status_writer_merges_with_atomic_write(tmp_path):
    path = tmp_path / "runtime-status.json"
    writer = RuntimeStatusWriter(path)

    writer.write({"state": "starting"})
    result = writer.merge({"auto_ocr": {"state": "captured"}})

    assert result["state"] == "starting"
    assert result["auto_ocr"]["state"] == "captured"
    assert "updated_at" in result
