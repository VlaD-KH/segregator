"""Дефект D2: текст исключения не должен нести содержимое экспорта.

Исключения из конвейера уходят в stderr и в вывод CLI (`cli.py`:
`Экспорт отклонён: {error}`), а оттуда — в переписку при отладке.

Правило берётся из `docs/DATA_BOUNDARY.md`, **инвариант 1**: «Ни один байт
содержимого документа не уходит в облако. Ни в переписку, …». Инвариант 4
(«Логи содержат идентификаторы…») здесь ни при чём — он про логгер;
ранняя редакция этих комментариев ссылалась на него ошибочно.

Тесты бьют по каждому месту, где раньше интерполировались данные, плюс
держат allowlist-инвариант по всему конвейеру — чтобы новый `raise` с
именем файла не проехал незамеченным.
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

# **Allowlist, не денилист.** Первая редакция перечисляла запрещённые имена
# (`relative`, `href`, `title`…) — то есть держалась на конвенции именования,
# которую ничто не проверяет. Adversarial-ревью показало дыру за минуту:
# в `export_reader` путь вложения зовётся `rel`, а весь сырой словарь
# сообщения — `item`; ни то, ни другое в денилист не входило, и
# `raise X(f"{rel}")` прошёл бы зелёным.
#
# Теперь наоборот: в аргументе `raise` разрешено ровно перечисленное, всё
# остальное роняет тест. Fails closed — новая переменная с данными не пройдёт
# просто потому, что её никто не догадался запретить.
ALLOWED_IN_RAISE = frozenset({
    "message_id",   # идентификатор сообщения — инвариант 1 его не запрещает
    "reason",       # имя класса пойманного исключения, не его текст
    "RESULT_JSON",  # константа модуля, литерал "result.json"
})

# Выдуманное имя, по которому видно утечку: если оно всплыло в тексте
# исключения — значит имя файла из экспорта туда попало.
SECRET_NAME = "tajna-faktura-kowalski"


def _document_path_modules() -> list[Path]:
    modules: list[Path] = []
    for package in DOCUMENT_PATH_PACKAGES:
        # rglob, не glob: `glob("*.py")` не заходит в подпакеты, и инвариант
        # тихо переставал бы действовать в тот день, когда в `ingest/`
        # появится `parsers/`. Найдено adversarial-ревью.
        modules.extend(sorted((SRC / "segregator" / package).rglob("*.py")))
    assert modules, "не найдено ни одного модуля конвейера — инвариант пустой"
    return modules


def _names_in_raise_args(source: str) -> list[tuple[int, str]]:
    """Имена, попадающие в текст исключения, с номерами строк.

    Смотрим три вещи:
    - **аргументы** конструктора (`node.exc.args`/`keywords`), но НЕ сам класс
      исключения — `ValueError` в `raise ValueError(...)` это тоже `ast.Name`;
    - **голый** `raise err` — так обходят проверку, собрав исключение заранее
      (`err = X(f"{relative}"); raise err`);
    - **`from exc`** — Python печатает цепочку `__cause__` в traceback, то есть
      санитизированная обёртка над исключением stdlib всё равно вынесет путь.
      Безопасная форма — `from None`.

    Внутри аргументов обходится всё поддерево, поэтому видны `f"{x}"`,
    `"a" + x`, `"{}".format(x)`, `x % y`, `foo.bar` (через базовое `foo`) и
    `link.get("href")` (через `link`).
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue

        if isinstance(node.exc, ast.Call):
            subtrees = list(node.exc.args) + [kw.value for kw in node.exc.keywords]
        else:
            # `raise err` — само имя и есть подозреваемое.
            subtrees = [node.exc]

        cause = node.cause
        if cause is not None and not (
            isinstance(cause, ast.Constant) and cause.value is None
        ):
            # `from exc`: traceback напечатает текст исходного исключения.
            subtrees.append(cause)

        for subtree in subtrees:
            for inner in ast.walk(subtree):
                if isinstance(inner, ast.Name):
                    found.append((node.lineno, inner.id))
    return found


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
    # номер сообщения — идентификатор, инвариант 1 его не запрещает.
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


def test_nul_in_href_raises_the_designed_error_not_a_bare_valueerror(tmp_path):
    """Регрессия от `unquote`, найденная adversarial-ревью прогона.

    `unquote("files/a%00.pdf")` превращает инертный литерал `%00` в настоящий
    NUL, и `resolve()` роняет `ValueError: embedded null character` —
    **не** `ExportPathError`. `cli.backfill` ловит только `FileNotFoundError`
    и `ExportPathError`, `normalize.backfill` — `try/finally` без `except`:
    голое исключение убивало весь прогон, а его traceback выносил наружу
    декодированное имя файла (pytest печатает locals кадра pathlib).
    """
    export = _html_export(tmp_path, href=f"files/{SECRET_NAME}%00.pdf")

    with pytest.raises(ExportPathError) as caught:
        list(iter_messages_html(export))

    # Именно ExportPathError, а не любой ValueError: подкласс ловится
    # обработчиком в cli.py, родитель — нет.
    assert type(caught.value) is ExportPathError
    assert SECRET_NAME not in str(caught.value)


def test_nul_in_json_file_field_raises_the_designed_error(tmp_path):
    # JSON умеет нести \\u0000 в строке — здесь `unquote` ни при чём,
    # экспозиция была и до него.
    export = _json_export(tmp_path, f"files/{SECRET_NAME}\x00.pdf")

    with pytest.raises(ExportPathError) as caught:
        list(iter_messages_json(export))

    assert type(caught.value) is ExportPathError
    assert SECRET_NAME not in str(caught.value)


def test_only_allowlisted_names_reach_exception_text_on_the_document_path():
    """Инвариант по всему конвейеру, а не по известным местам.

    Разбор через ast, а не регуляркой: регулярка над текстом `raise`
    пропускает конкатенацию, `.format()`, `%`-формат и многострочный
    `raise X(...) from exc` — то есть ровно те формы, которыми утечка
    и вернётся.

    Allowlist, а не денилист: денилист держится на именовании переменных,
    и его пробили за минуту (`rel`, `item` в JSON-читателе). Здесь всё, что
    не разрешено явно, роняет тест.
    """
    offenders: list[str] = []
    for path in _document_path_modules():
        rel_path = path.relative_to(SRC).as_posix()
        for lineno, name in _names_in_raise_args(path.read_text(encoding="utf-8")):
            if name not in ALLOWED_IN_RAISE:
                offenders.append(f"{rel_path}:{lineno} выносит {name!r} в текст исключения")

    assert not offenders, (
        "в тексте исключения на пути обработки документа разрешены только "
        f"{sorted(ALLOWED_IN_RAISE)}; найдено: " + "; ".join(sorted(set(offenders)))
    )


def test_the_invariant_catches_every_evasion_shape():
    """Проверка самого инварианта. Без неё предыдущий тест мог бы быть зелёным
    просто потому, что слеп, — и выглядел бы доказательством чистоты.

    Формы ниже — не выдумка: каждую предложило adversarial-ревью как способ
    пронести данные мимо первой (денилист + регулярка) редакции.
    """
    evasions = [
        'raise ValueError(f"плохо: {relative}")',            # f-строка
        'raise ValueError("плохо: " + relative)',            # конкатенация
        'raise ValueError("плохо: {}".format(href))',        # .format()
        'raise ValueError("плохо: %s" % source)',            # %-формат
        'raise ValueError(chr(10).join([body]))',            # внутри вызова
        'raise ValueError(f"{rel}")',                        # имя вне денилиста
        'raise ValueError(f"{item}")',                       # сырой словарь сообщения
        'raise ValueError(f"{attachment.path}")',            # доступ к атрибуту
        'raise ValueError(f"{link.get(chr(104))}")',         # результат вызова
        'p = decoded\nraise ValueError(f"{p}")',             # алиас
        'err = ValueError(f"{relative}")\nraise err',        # отложенная сборка
        'raise ValueError("чисто") from exc',                # __cause__ в traceback
    ]
    for snippet in evasions:
        names = {n for _, n in _names_in_raise_args(snippet)}
        assert names - ALLOWED_IN_RAISE, f"инвариант не увидел утечку: {snippet!r}"

    # И не срабатывает на честных формах.
    clean = [
        'raise ValueError("Путь вложения ведёт за пределы корня")',
        'raise ValueError(f"Не разобрана дата сообщения {message_id}")',
        'raise ExportPathError(f"не разобрать: {reason}") from None',
        'raise FileNotFoundError(f"нет {RESULT_JSON}")',
    ]
    for snippet in clean:
        names = {n for _, n in _names_in_raise_args(snippet)}
        assert not (names - ALLOWED_IN_RAISE), f"ложное срабатывание: {snippet!r}"
