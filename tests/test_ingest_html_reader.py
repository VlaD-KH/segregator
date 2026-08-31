"""Читатель HTML-экспорта Telegram Desktop.

Структура снята разведчиком probe/02_html_structure.py с настоящего экспорта:
message default clearfix [joined] / message service, дата в
div.pull_right.date.details[title], вложения в a.media_file[href] и
a.photo_wrap[href].
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from segregator.ingest.html_reader import (
    ExportPathError,
    iter_messages_html,
    read_channel_name,
    resolve_chat_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "export_html"


def test_reads_both_pagination_pages():
    messages = list(iter_messages_html(FIXTURE))
    ids = [m.message_id for m in messages]
    # messages.html даёт 101..106, messages2.html — 107. Служебные пропущены.
    assert ids == [101, 102, 103, 104, 105, 106, 107]


def test_service_messages_are_skipped():
    ids = [m.message_id for m in iter_messages_html(FIXTURE)]
    # id="message-1" и "message-2" — служебные, их номера отрицательны/служебны.
    assert all(i > 0 for i in ids)
    assert len(ids) == 7


def test_html_attachment_in_files_is_not_parsed_as_export():
    """files/tracker_widget.html — вложение, а не страница экспорта.

    В настоящем экспорте там лежит React-приложение, присланное файлом. Если
    читатель зайдёт в files/, он примет его за переписку: в ловушке есть
    div.message default clearfix с id=message9999.
    """
    ids = [m.message_id for m in iter_messages_html(FIXTURE)]
    assert 9999 not in ids


def test_joined_messages_are_read_like_any_other():
    by_id = {m.message_id: m for m in iter_messages_html(FIXTURE)}
    # 102, 104, 106 имеют класс "joined" — это подряд идущие от одного
    # отправителя, а не другой тип сообщения.
    assert by_id[102].attachments and by_id[102].attachments[0].path.suffix == ".jpg"
    assert by_id[104].attachments


def test_dates_are_parsed_to_iso_utc():
    by_id = {m.message_id: m for m in iter_messages_html(FIXTURE)}
    # "14.01.2025 10:12:00 UTC+01:00" -> 09:12 UTC
    assert by_id[101].sent_at == "2025-01-14T09:12:00+00:00"
    # "02.04.2025 12:00:00 UTC+02:00" -> 10:00 UTC
    assert by_id[107].sent_at == "2025-04-02T10:00:00+00:00"


def test_file_and_photo_attachments_resolve():
    by_id = {m.message_id: m for m in iter_messages_html(FIXTURE)}

    doc = by_id[101].attachments[0]
    assert doc.path == FIXTURE / "files" / "doc_a.pdf"
    assert doc.exists is True
    assert doc.orig_name == "faktura-a.pdf"

    photo = by_id[102].attachments[0]
    assert photo.path == FIXTURE / "photos" / "photo_1.jpg"
    assert photo.exists is True


def test_missing_attachment_is_flagged_not_fatal():
    by_id = {m.message_id: m for m in iter_messages_html(FIXTURE)}
    assert by_id[104].attachments[0].exists is False
    assert 107 in by_id, "итерация должна дойти до конца"


def test_message_without_attachment_has_text_only():
    by_id = {m.message_id: m for m in iter_messages_html(FIXTURE)}
    assert by_id[106].attachments == []
    assert by_id[106].body == "tylko tekst, bez zalacznika"


def test_body_is_none_when_no_caption():
    by_id = {m.message_id: m for m in iter_messages_html(FIXTURE)}
    # У 103 нет div.text — подписи не было.
    assert by_id[103].body is None


def test_channel_name_and_stable_chat_id():
    assert read_channel_name(FIXTURE) == "ksiegowosc-test"
    first = resolve_chat_id(FIXTURE)
    # HTML-экспорт не содержит числового chat_id, поэтому он выводится из
    # имени канала — детерминированно, иначе повторный прогон создал бы дубли.
    assert isinstance(first, int)
    assert resolve_chat_id(FIXTURE) == first


def _export_with(tmp_path: Path, href: str) -> Path:
    export = tmp_path / "export"
    (export / "files").mkdir(parents=True)
    (export / "files" / "ok.pdf").write_bytes(b"ok")
    (export / "messages.html").write_text(
        '<html><body><div class="page_header"><div class="text bold">t</div></div>'
        '<div class="message default clearfix" id="message1">'
        '<div class="body"><div class="pull_right date details" '
        'title="14.01.2025 10:12:00 UTC+01:00">10:12</div>'
        f'<div class="media_wrap clearfix"><a class="media clearfix pull_left '
        f'block_link media_file" href="{href}"><div class="body">'
        '<div class="title bold">x</div></div></a></div></div></div></body></html>',
        encoding="utf-8",
    )
    return export


def test_path_traversal_is_rejected(tmp_path):
    export = _export_with(tmp_path, "../../../evil.pdf")
    with pytest.raises(ExportPathError):
        list(iter_messages_html(export))


def test_absolute_path_is_rejected(tmp_path):
    export = _export_with(tmp_path, "C:/Windows/System32/drivers/etc/hosts")
    with pytest.raises(ExportPathError):
        list(iter_messages_html(export))


def test_external_url_is_not_treated_as_attachment(tmp_path):
    """В настоящем экспорте есть ссылки на unpkg.com — это не вложения."""
    export = _export_with(tmp_path, "https://unpkg.com/react@18/umd/react.js")
    messages = list(iter_messages_html(export))
    assert messages[0].attachments == []


def test_missing_messages_html_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        list(iter_messages_html(empty))


def test_percent_encoded_href_resolves_to_the_real_file(tmp_path):
    """Дефект D1: Telegram кодирует пробелы и не-ASCII в href.

    Без `unquote` резолвился путь с литеральным `%20`, файл не находился, и
    вложение ТИХО уходило в missing_files — на польских именах это давало бы
    десятки ложных «не найдено», неотличимых от невыгруженного медиа.
    """
    export = _export_with(tmp_path, "files/faktura%20nr%201.pdf")
    (export / "files" / "faktura nr 1.pdf").write_bytes(b"ok")

    attachment = list(iter_messages_html(export))[0].attachments[0]

    assert attachment.path.name == "faktura nr 1.pdf"
    assert attachment.exists is True


def test_percent_encoded_diacritics_resolve(tmp_path):
    # Польская диакритика в имени файла — обычное дело в этом канале.
    export = _export_with(tmp_path, "files/za%C5%82%C4%85cznik.pdf")
    (export / "files" / "załącznik.pdf").write_bytes(b"ok")

    attachment = list(iter_messages_html(export))[0].attachments[0]

    assert attachment.path.name == "załącznik.pdf"
    assert attachment.exists is True


def test_percent_encoded_traversal_is_still_rejected(tmp_path):
    """Декодирование не должно открывать дыру в защите от выхода за корень.

    Порядок обязан быть decode → resolve → is_relative_to: если проверять
    ДО декодирования, `%2e%2e%2f` проскочит как обычное имя файла.
    """
    export = _export_with(tmp_path, "files/%2e%2e%2f%2e%2e%2fevil.pdf")
    with pytest.raises(ExportPathError):
        list(iter_messages_html(export))


def test_percent_encoded_backslash_traversal_is_rejected(tmp_path):
    # Windows-разделитель в кодированном виде — тот же обход другим байтом.
    export = _export_with(tmp_path, "files/%2e%2e%5c%2e%2e%5cevil.pdf")
    with pytest.raises(ExportPathError):
        list(iter_messages_html(export))


def test_nested_chatexport_dir_is_not_scanned(tmp_path):
    """Настоящий JDG/ содержит и messages.html, и ChatExport_*/messages.html.

    Это два РАЗНЫХ экспорта. Читатель обязан разбирать только корень того,
    что ему дали, иначе сообщения удвоятся.
    """
    export = _export_with(tmp_path, "files/ok.pdf")
    nested = export / "ChatExport_2026-08-31"
    nested.mkdir()
    shutil.copy(export / "messages.html", nested / "messages.html")

    ids = [m.message_id for m in iter_messages_html(export)]
    assert ids == [1], "вложенный экспорт не должен подхватываться"
