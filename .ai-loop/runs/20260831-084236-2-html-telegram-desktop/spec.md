# Spec — `segregator backfill` работает на реальном HTML-экспорте, не только на JSON

> Написан в DEFINE, после того как половина кода уже существует (читатель
> `html_reader.py` собран и одобрен в BUILD, seq 22–23). Критерии ниже
> написаны от **проблемы**, а не от кода, и затем сверены с кодом —
> расхождения вынесены в раздел 6 и 8.

| | |
|---|---|
| Run id | `20260831-084236-2-html-telegram-desktop` |
| Source idea | `Э2: читатель HTML-экспорта Telegram Desktop` |
| Author | agent (Claude Code) + vlad (подтвердил формулировку проблемы) |
| Status | accepted |

## 1. Problem

Реальный экспорт Telegram-канала «бухгалтерия» сделан в **HTML** (`messages.html`,
`messages2.html`, …), а не в JSON. Это установлено переписью Э0
(`probe/01_host_and_export.ps1`, `probe/02_html_structure.py`) и подтверждено
пользователем: `python -m segregator backfill --dry-run` падает сразу —

```
Экспорт не найден: Не найден result.json в C:\...\JDG.
Если экспорт сделан в HTML, разбор устроен иначе — см. SPEC.md.
```

Весь конвейер Э2 (`export_reader.py` → `normalize.py` → `blobs.py` → БД)
написан под `result.json` и `ijson`. К реальным данным он неприменим:
`backfill` не доходит ни до одной строки. Э3+ (extract, classify, route)
стоят за Э2 и тоже заблокированы.

Что **уже** сделано в этом прогоне (BUILD, одобрено человеком seq 23):

- `src/segregator/ingest/html_reader.py` — потоковый парсер `messages*.html`
  на `beautifulsoup4` (`html.parser`, чистый Python). Отдаёт те же
  `RawMessage` / `RawAttachment`, что и JSON-читатель.
- `tests/fixtures/export_html/` — синтетическая фикстура (2 страницы
  пагинации, служебные записи, ловушка `files/tracker_widget.html`,
  вложение-дубль, отсутствующий файл), зарегистрирована в манифесте.
- `tests/test_ingest_html_reader.py` — 15 тестов, зелёные.

Чего **не** сделано и что закрывает этот прогон: `html_reader` никуда не
подключён. `normalize.backfill` по-прежнему жёстко импортирует JSON-читатель
(`normalize.py:19`). Между «экспорт на диске» и «читатель» нет слоя, который
выбирает формат.

## 2. Goals

1. `segregator backfill` на каталоге с `messages*.html` (без `result.json`)
   доходит до конца и заполняет `messages` / `blobs` / `attachments`.
2. `normalize.py` не знает, какой формат экспорта — выбор формата живёт в
   одном модуле, который можно указать пальцем.
3. JSON-путь и все 48 существующих тестов Э1/Э2 остаются зелёными без правок
   их ассертов.

## 3. Non-goals

- **Не** переписывать и не удалять `export_reader.py` (JSON-читатель). Оба
  формата поддерживаются: JSON нужен синтетическим фикстурам Э2 и остаётся
  валидным выходом Telegram Desktop.
- **Не** трогать live-приём Э6 — он подключится к `normalize` позже и через
  тот же слой.
- **Не** менять схему БД, `blobs.py`, каскад классификации.
- **Не** определять MIME вложений: HTML-экспорт его не сообщает, это задача Э3
  (`mime=None` — сознательно).
- **Не** запускать боевой `backfill` на настоящем `JDG/` в рамках этого
  прогона — это делает человек после мержа.
- **Не** решать `ARCHIVE_DIR` (открытый вопрос проекта, не блокирует).

## 4. Requirements

| # | Requirement | Priority | Zone (predicted) |
|---|---|---|---|
| R1 | Слой выбора читателя: `result.json` есть → JSON-читатель; иначе `messages*.html` в корне → HTML-читатель; иначе — `FileNotFoundError` с внятным текстом про оба формата | P0 | E (`src/segregator/ingest/`) |
| R2 | `normalize.backfill` берёт `iter_messages` / `read_chat_id` из слоя выбора, а не напрямую из `export_reader` | P0 | E (`src/segregator/ingest/normalize.py`) |
| R3 | `backfill` против HTML-фикстуры: счётчики сходятся, дубль-файл даёт один блоб и две ссылки, второй прогон — ноль нового, отсутствующий файл пропускается и считается | P0 | T (`tests/`) |
| R4 | Юнит-тесты слоя выбора: три ветки (JSON / HTML / ничего) | P0 | T (`tests/`) |
| R5 | `SPEC.md` и `tasks/plan.md` приведены к HTML-реальности; Open Question про формат снят | P1 | E (`SPEC.md`, `tasks/`) |

Порядок BUILD: R1 → R2 → R4 → R3 → R5.

## 5. Acceptance criteria

### A1 (R1, R2) — HTML-экспорт доходит до базы

```
Given  каталог экспорта содержит messages.html и messages2.html, папку
       files/ с doc_a.pdf, doc_b.pdf, umowa.docx и папку photos/ с
       photo_1.jpg, и НЕ содержит result.json
       (фикстура tests/fixtures/export_html/, 7 сообщений: id 101..107,
        2 служебные записи пропускаются)
When   вызывается segregator.ingest.normalize.backfill(archive, export_dir)
Then   stats.messages_added == 7
  и    stats.attachments_added == 5
  и    stats.missing_files == 1   (files/brakujacy.pdf — ссылка есть, файла нет)
  и    SELECT COUNT(*) даёт messages=7, blobs=4, attachments=5
```

### A2 (R3) — дедупликация по HTML-экспорту

```
Given  та же фикстура; files/doc_a.pdf прислан в сообщениях 101 и 103
When   backfill(archive, export_dir) отработал
Then   в attachments две строки с одним и тем же sha256
  и    в blobs ровно 4 строки
  и    на диске в blobs/ ровно 4 файла (второй копии doc_a.pdf нет)
```

### A3 (R3) — идемпотентность

```
Given  backfill(archive, export_dir) по HTML-фикстуре уже отработал один раз
When   backfill(archive, export_dir) вызывается второй раз подряд
Then   stats.messages_added == 0 и stats.attachments_added == 0 и
       stats.blobs_new == 0
  и    stats.messages_skipped == 7
  и    число строк в messages/blobs/attachments и число файлов в blobs/
       не изменилось
```

### A4 (R3) — dry-run предсказывает боевой прогон точно

```
Given  чистый архив и HTML-фикстура
When   predicted = backfill(..., dry_run=True); actual = backfill(...)
Then   predicted.as_dict() == actual.as_dict()
  и    после dry_run в messages/blobs/attachments ноль строк
```

### A5 (R1, R4) — выбор читателя

```
Given  каталог с одним лишь result.json
When   ingest.<layer>.read_chat_id(dir) и list(iter_messages(dir))
Then   отрабатывает JSON-читатель (chat_id == 1900000001 на export_min)

Given  каталог с messages.html и без result.json
When   то же
Then   отрабатывает HTML-читатель (chat_id детерминирован от имени канала,
       отрицательный, стабилен между вызовами)

Given  пустой каталог (ни result.json, ни messages*.html)
When   list(iter_messages(dir))
Then   FileNotFoundError, в тексте упомянуты и result.json, и messages*.html
```

### A6 (R2) — регрессия JSON-пути

```
Given  фикстура tests/fixtures/export_min/ (JSON, result.json)
When   python -m pytest tests/ -q
Then   все ранее зелёные тесты Э1/Э2 (48 шт.) остаются зелёными,
       ни один ассерт в них не правился
```

### A7 (R1) — path traversal остаётся отбитым на обоих путях

```
Given  HTML-экспорт, где href вложения = "../../../evil.pdf" либо
       абсолютный путь "C:/Windows/..."
When   backfill / iter_messages проходит по нему
Then   ExportPathError, ни одной записи и ни одного файла за корнем экспорта
       (уже покрыто в test_ingest_html_reader.py — слой выбора не должен
        это ослабить)
```

## 6. How this could pass and still be wrong

- **A1 сходится, потому что числа в тест подогнали под то, что вернул код.**
  Защита: числа в этом spec выведены вручную из фикстуры (7 `div.message`
  с числовым id; 6 ссылок на вложения, из них `brakujacy.pdf` отсутствует →
  5 добавленных; `doc_a.pdf` дважды → 4 блоба). Тест сверяется с этим
  разделом, а не наоборот. VERIFY обязан подтвердить вывод по фикстуре
  независимо.
- **Слой выбора «поддерживает HTML», но фактически всегда зовёт HTML-ветку,
  а JSON-тесты зелены по другой причине.** Защита: A5 первой веткой и A6 —
  JSON-фикстура должна идти именно через JSON-читатель; тест на диспетчер
  проверяет `chat_id == 1900000001` (это значение только из `result.json`,
  HTML-ветка дала бы отрицательное).
- **Идемпотентность «работает», потому что второй прогон падает с
  исключением до записи, а не потому что ON CONFLICT отрабатывает.**
  Защита: A3 требует `exit 0` смысл (ненулевой diff — не ошибка) и явные
  нули в счётчиках *добавления* при `messages_skipped == 7`.
- **`missing_files` считается, но запись всё равно создаётся** (битая ссылка
  на blob). Защита: A1 фиксирует `attachments == 5`, а не 6; VERIFY проверит,
  что для `brakujacy.pdf` нет строки в `attachments` и нет строки в `blobs`.
- **dry-run считает дубль дважды** (нечего коллидировать на диске). Защита:
  A4 — `predicted.as_dict() == actual.as_dict()` побайтово, включая
  `blobs_new` / `blobs_deduped`.
- **Диспетчер молча рекурсивно сканирует `files/` и `ChatExport_*/`** и
  удваивает переписку. Защита: это уже покрыто
  `test_nested_chatexport_dir_is_not_scanned` и
  `test_html_attachment_in_files_is_not_parsed_as_export`; слой выбора не
  должен обходить `html_reader.export_pages`, а обязан звать его.
- **Тест на диспетчер и код диспетчера в одном коммите** — зелёный тест не
  доказывает поведение. Защита: T и не-T в одном диффе эскалируются
  классификатором; VERIFY (falsification) ломает диспетчер и смотрит, какой
  тест это замечает.

## 7. Risk and rollback

**Зоны:** E (`ingest/` — новый модуль выбора + правка импорта в `normalize.py`;
`SPEC.md`; `tasks/`) и T (`tests/`). `export_reader.py` (зона R) **не
трогается**. `cli.py` (зона I) **не трогается**: текст ошибки «Экспорт не
найден: {error}» уже общий, новый `FileNotFoundError` в него ложится как есть.

Кумулятивный дифф прогона включает уже одобренную партию `html_reader.py` +
`.gitignore` (зона P, из прошлой сессии) → классификация почти наверняка
вернёт `require_human`. Это ожидаемо; закрывается `/ai-approve`.

**Если изменение неверно в бою:** худший случай — `backfill` пишет неверные
строки в свежую БД либо падает на середине. БД — рабочий артефакт Э2, не
юридический архив (архив появляется в Э5, дерево на диске). Транзакция в
`normalize.backfill` откатывается целиком при обрыве; блобы в
content-addressed хранилище переиспользуются по sha256. Откат: `git revert`
коммитов прогона + удалить `archive/segregator.db` и `archive/blobs/`,
прогнать `segregator init` заново.

**Контур данных:** новый модуль — только диспетчеризация путей, без сети, без
логирования содержимого. `boundary-guard` обязателен перед коммитом (трогает
`src/` и `tests/`).

## 8. Open questions

Нет открытых вопросов, блокирующих BUILD.

Снимается по итогам Э0 (перепись):

- ~~Формат экспорта не подтверждён (JSON или HTML).~~ → **HTML**
  (`messages.html` + пагинация). Old SPEC.md Open Question #1 удаляется.

Остаются открытыми на уровне проекта, **не** блокируют этот прогон:

- `ARCHIVE_DIR` не выбран. 40 МБ помещаются куда угодно; для тестов
  используется `tmp_path`.
- В `.ai-loop/ledger.jsonl` записи фазы BUILD этого прогона записаны под
  `run_id: "R"` (литеральный placeholder), а не под полным id. Плюс два
  чужих прогона Why_Ai. Ledger append-only — чистит человек, не цикл;
  на BUILD не влияет, отмечено для REVIEW/SHIP.
