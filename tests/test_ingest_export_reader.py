"""Т2 — потоковый читатель экспорта Telegram Desktop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segregator.ingest.export_reader import (
    ExportPathError,
    RawAttachment,
    RawMessage,
    iter_messages,
    read_chat_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "export_min"


def test_iter_messages_skips_service_messages():
    messages = list(iter_messages(FIXTURE))
    # В фикстуре 6 записей, одна из них type=service.
    assert [m.message_id for m in messages] == [1, 3, 4, 5, 6]
    assert all(isinstance(m, RawMessage) for m in messages)


def test_iter_messages_resolves_file_and_photo_attachments():
    by_id = {m.message_id: m for m in iter_messages(FIXTURE)}

    doc = by_id[1].attachments[0]
    assert isinstance(doc, RawAttachment)
    assert doc.path == FIXTURE / "files" / "doc_a.pdf"
    assert doc.exists is True
    assert doc.orig_name == "faktura-a.pdf"

    photo = by_id[3].attachments[0]
    assert photo.path == FIXTURE / "photos" / "photo_1.jpg"
    assert photo.exists is True


def test_missing_attachment_is_flagged_not_fatal():
    by_id = {m.message_id: m for m in iter_messages(FIXTURE)}
    missing = by_id[5].attachments[0]
    assert missing.exists is False
    # Итерация дошла до конца, несмотря на отсутствующий файл.
    assert 6 in by_id


def test_message_carries_body_and_timestamp():
    by_id = {m.message_id: m for m in iter_messages(FIXTURE)}
    first = by_id[1]
    assert first.body == "faktura styczen"
    # ISO-8601 UTC, как требует схема БД.
    assert first.sent_at.startswith("2025-01-14T")
    assert first.sent_at.endswith("+00:00")


def test_read_chat_id_returns_top_level_id():
    assert read_chat_id(FIXTURE) == 1900000001


def _make_export(tmp_path: Path, messages: list[dict]) -> Path:
    export = tmp_path / "export"
    (export / "files").mkdir(parents=True)
    (export / "files" / "ok.pdf").write_bytes(b"ok")
    payload = {"name": "t", "type": "public_channel", "id": 42, "messages": messages}
    (export / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    return export


def test_path_traversal_is_rejected(tmp_path):
    export = _make_export(
        tmp_path,
        [{"id": 1, "type": "message", "date_unixtime": "1736849520",
          "file": "../../../evil.pdf", "text_entities": []}],
    )
    # Путь приходит из данных, значит может быть враждебным.
    with pytest.raises(ExportPathError):
        list(iter_messages(export))


def test_absolute_path_is_rejected(tmp_path):
    export = _make_export(
        tmp_path,
        [{"id": 1, "type": "message", "date_unixtime": "1736849520",
          "file": "C:\\Windows\\System32\\drivers\\etc\\hosts", "text_entities": []}],
    )
    with pytest.raises(ExportPathError):
        list(iter_messages(export))


def test_message_without_attachment_yields_empty_list(tmp_path):
    export = _make_export(
        tmp_path,
        [{"id": 7, "type": "message", "date_unixtime": "1736849520",
          "text": "tylko tekst", "text_entities": []}],
    )
    messages = list(iter_messages(export))
    assert len(messages) == 1
    assert messages[0].attachments == []


def test_file_marked_as_unavailable_by_telegram_is_skipped(tmp_path):
    # Telegram пишет "(File not included. Change data exporting settings...)"
    # вместо пути, когда медиа не выгружено. Это не путь и не должно им стать.
    export = _make_export(
        tmp_path,
        [{"id": 8, "type": "message", "date_unixtime": "1736849520",
          "file": "(File not included. Change data exporting settings to download.)",
          "text_entities": []}],
    )
    messages = list(iter_messages(export))
    assert messages[0].attachments == []


def test_missing_result_json_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        list(iter_messages(empty))
