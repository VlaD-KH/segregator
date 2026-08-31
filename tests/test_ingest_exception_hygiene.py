"""Дефект D2: текст исключения не должен нести содержимое экспорта.

Исключения из `ingest/` уходят в stderr и в вывод CLI (`cli.py`:
`Экспорт отклонён: {error}`), а оттуда — в переписку при отладке. Это тот
самый канал, который `docs/DATA_BOUNDARY.md` инвариант 4 закрывает: наружу
идут идентификаторы, категории и счётчики — не имена файлов, не тексты,
не сырые поля документа.

Тесты бьют по каждому месту, где раньше интерполировались данные, плюс
держат общий инвариант по всем `raise` в каталоге — чтобы новый `raise`
с `{filename}` не проехал незамеченным.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from segregator.ingest.blobs import store_blob
from segregator.ingest.export_reader import ExportPathError
from segregator.ingest.export_reader import iter_messages as iter_messages_json
from segregator.ingest.html_reader import iter_messages_html

SRC = Path(__file__).resolve().parents[1] / "src"

# Конвейер обработки документа: ingest → extract → classify → route → serve.
# Исключение отсюда несёт контекст конкретного документа, поэтому текст обязан
# быть чистым.
#
# `cli.py`, `config.py`, `paths.py` намеренно ВНЕ инварианта: их дело —
# показать оператору его собственную конфигурацию (`segregator doctor` ради
# этого и существует), и вызывает их человек осознанно, а не конвейер посреди
# разбора чужих данных. Это решение, а не недосмотр — см. F-9.3 в
# docs/FUNCTIONAL_CHECKLIST.md.
DOCUMENT_PATH_PACKAGES = ("ingest", "extract", "classify", "route", "serve")

# Имена, за которыми стоят данные экспорта: путь вложения, href, сырой текст,
# подпись, тело сообщения, корень экспорта.
FORBIDDEN_IN_RAISE = frozenset(
    {"relative", "decoded", "href", "title", "source", "body", "text", "export_dir"}
)

# Выдуманное имя, по которому видно утечку: если оно всплыло в тексте
# исключения — значит имя файла из экспорта туда попало.
SECRET_NAME = "tajna-faktura-kowalski"


def _document_path_modules() -> list[Path]:
    modules: list[Path] = []
    for package in DOCUMENT_PATH_PACKAGES:
        modules.extend(sorted((SRC / "segregator" / package).glob("*.py")))
    assert modules, "не найдено ни одного модуля конвейера — инвариант пустой"
    return modules


def _html_export(tmp_path: Path, *, href: str = "files/ok.pdf",
                 date_title: str = "14.01.2025 10:12:00 UTC+01:00") -> Path:
    export = tmp_path / "export"
    (export / "files").mkdir(parents=True)
    (export / "files" / "ok.pdf").write_bytes(b"ok")
    (export / "messages.html").write_text(
        '<html><body><div class="page_header"><div class="text bold">t</div></div>'
        '<div class="message default clearfix" id="message1"><div class="body">'
        f'<div class="pull_right date details" title="{date_title}">10:12</div>'
        f'<div class="media_wrap clearfix"><a class="media_file" href="{href}">'
        '<div class="title bold">x</div></a></div></div></div></body></html>',
        encoding="utf-8",
    )
    return export


def _json_export(tmp_path: Path, file_field: str) -> Path:
    export = tmp_path / "export"
    (export / "files").mkdir(parents=True)
    payload = {
        "name": "t", "type": "public_channel", "id": 42,
        "messages": [{"id": 1, "type": "message", "date_unixtime": "1736849520",
                      "file": file_field, "text_entities": []}],
    }
    (export / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    return export


def test_html_path_rejection_does_not_name_the_file(tmp_path):
    export = _html_export(tmp_path, href=f"../../../{SECRET_NAME}.pdf")

    with pytest.raises(ExportPathError) as caught:
        list(iter_messages_html(export))

    message = str(caught.value)
    assert SECRET_NAME not in message
    assert ".." not in message
    # Причина обязана остаться понятной, иначе санитизация превратила
    # ошибку в бесполезную.
    assert "корня экспорта" in message


def test_json_path_rejection_does_not_name_the_file(tmp_path):
    export = _json_export(tmp_path, f"../../../{SECRET_NAME}.pdf")

    with pytest.raises(ExportPathError) as caught:
        list(iter_messages_json(export))

    message = str(caught.value)
    assert SECRET_NAME not in message
    assert ".." not in message
    assert "корня экспорта" in message


def test_unparsed_date_reports_message_id_not_the_raw_value(tmp_path):
    # Сырая строка даты — поле документа. В тексте её быть не должно;
    # номер сообщения — идентификатор, он разрешён инвариантом 4.
    export = _html_export(tmp_path, date_title="Kowalski 2025 nieprawidlowa data")

    with pytest.raises(ValueError) as caught:
        list(iter_messages_html(export))

    message = str(caught.value)
    assert "Kowalski" not in message
    assert "nieprawidlowa" not in message
    assert "1" in message, "номер сообщения должен остаться — по нему ищут"


def test_missing_blob_source_does_not_name_the_document(tmp_path):
    missing = tmp_path / "files" / f"{SECRET_NAME}.pdf"

    with pytest.raises(FileNotFoundError) as caught:
        store_blob(tmp_path / "archive", missing)

    assert SECRET_NAME not in str(caught.value)


def test_no_raise_on_the_document_path_interpolates_export_data():
    """Инвариант по всему конвейеру обработки документа, а не по четырём
    известным местам. Ловит новый `raise` с именем файла, который иначе прошёл
    бы ревью: точечные тесты выше проверяют только то, что уже чинили.

    Разбор через `ast`, а не регуляркой. Регулярка вида
    `raise\\s+\\w+\\((.*?)\\)` пропускает конкатенацию (`"плохо: " + relative`),
    `.format(href)`, многострочный `raise X(...) from exc` и вложенные скобки
    в f-строке — то есть ровно те формы, которыми утечка и вернётся.
    """
    offenders: list[str] = []

    for path in _document_path_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            # Всё поддерево аргументов исключения: f-строка, конкатенация,
            # .format(), вызов хелпера — любая форма попадёт сюда.
            for inner in ast.walk(node.exc):
                if isinstance(inner, ast.Name) and inner.id in FORBIDDEN_IN_RAISE:
                    offenders.append(
                        f"{path.relative_to(SRC).as_posix()}:{node.lineno} "
                        f"выносит {inner.id!r} в текст исключения"
                    )

    assert not offenders, (
        "данные документа в тексте исключения на пути обработки: "
        + "; ".join(sorted(set(offenders)))
    )


def test_the_invariant_actually_catches_every_leak_shape():
    """Проверка самого инварианта: он должен ловить не только f-строку.

    Без этого предыдущий тест мог бы быть зелёным просто потому, что не умеет
    видеть утечку — а выглядел бы как доказательство её отсутствия.
    """
    leaks = [
        'raise ValueError(f"плохо: {relative}")',        # f-строка
        'raise ValueError("плохо: " + relative)',         # конкатенация
        'raise ValueError("плохо: {}".format(href))',     # .format()
        'raise ValueError("плохо: %s" % source)',         # %-формат
        'raise ValueError(f"a{title}b") from exc',        # from-цепочка
        'raise ValueError("\\n".join([body]))',           # внутри вызова
    ]
    for snippet in leaks:
        tree = ast.parse(snippet)
        found = [
            inner.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Raise) and node.exc is not None
            for inner in ast.walk(node.exc)
            if isinstance(inner, ast.Name) and inner.id in FORBIDDEN_IN_RAISE
        ]
        assert found, f"инвариант не увидел утечку: {snippet}"

    # И не срабатывает на честном тексте без данных.
    clean = ast.parse('raise ValueError("Путь вложения ведёт за пределы корня")')
    assert not [
        inner
        for node in ast.walk(clean)
        if isinstance(node, ast.Raise) and node.exc is not None
        for inner in ast.walk(node.exc)
        if isinstance(inner, ast.Name) and inner.id in FORBIDDEN_IN_RAISE
    ]
