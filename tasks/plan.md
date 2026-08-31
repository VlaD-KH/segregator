# План: Э2 «Бэкфилл»

Источник требований — `SPEC.md` (раздел Success Criteria). Задачи нарезаны
вертикально: каждая — рабочий путь целиком со своим тестом, а не слой.

## Компоненты и зависимости

```
config.Settings ─┐                       ┌── ingest.export_reader ◄── result.json (ijson)
db.get_connection├─► ingest.normalize ◄──┤   ingest.html_reader   ◄── messages*.html (bs4)
paths.ensure_tree┘         │             └── ingest.export (выбор формата по каталогу)
                           ▼
                    ingest.blobs (sha256, content-addressed)
                           │
                           ▼
                    cli.backfill (--dry-run, --limit)
```

Порядок обязателен: `blobs` не зависит ни от чего, читатели не зависят
от БД, `normalize` связывает всё через `ingest.export` (слой выбора формата),
CLI — сверху. Т1 и Т2 независимы; Т3 требует обеих; Т4 требует Т3; Т5
(HTML-читатель + слой выбора) требует Т3 и переиспользует `normalize`/`blobs`
без правок.

## Риски и что с ними делаем

| Риск | Митигация |
|---|---|
| ~~Формат экспорта окажется HTML~~ — подтверждено Э0: экспорт **в HTML** | Т5: `html_reader.py` + `ingest.export` (слой выбора). `normalize`/`blobs`/`cli` не тронуты, `export_reader.py` (JSON) оставлен как есть |
| Путь вложения из данных уводит запись за корень экспорта | Т2: резолв + проверка `is_relative_to`, отдельный тест |
| Прерванный прогон оставляет обрезанный блоб | Т1: запись через `.part` + атомарный `replace()` |
| Огромный `result.json` не влезает в память | Т2: `ijson.items(f, "messages.item")`, никогда `json.load` |
| Повторный прогон дублирует данные | Т3: `INSERT ... ON CONFLICT DO NOTHING` по естественным ключам схемы |

## Контрольные точки

- После Т1 и Т2: `pytest` зелёный, БД ещё не затронута.
- После Т3: приёмка 1–3 и 5 из `SPEC.md` доказана тестами.
- После Т4: приёмка целиком, включая прогон CLI дважды подряд.
- Перед коммитом каждой задачи: `boundary-guard` (правило `CLAUDE.md`).

## Задачи

### Т1 — content-addressed хранилище блобов
Чистая функция над файловой системой, без БД и без конфига.
- Файлы: `src/segregator/ingest/blobs.py`, `tests/test_ingest_blobs.py`
- Acceptance: `store_blob()` возвращает `(sha256, path, was_new)`; повторный
  вызов на том же содержимом не создаёт второй файл и возвращает
  `was_new=False`; запись атомарна (временный `.part`, затем `replace`).
- Verify: `python -m pytest tests/test_ingest_blobs.py -v`

### Т2 — потоковый читатель экспорта
- Файлы: `src/segregator/ingest/export_reader.py`,
  `tests/fixtures/export_min/`, `tests/fixtures/manifest.json`,
  `tests/test_ingest_export_reader.py`, `pyproject.toml` (+`ijson`)
- Acceptance: `iter_messages(export_dir)` отдаёт `RawMessage` потоково через
  `ijson`; вложения резолвятся относительно корня экспорта; путь вне корня
  отвергается (`ExportPathError`); отсутствующий файл помечается, а не роняет
  итерацию; служебные сообщения (`type != "message"`) пропускаются.
- Verify: `python -m pytest tests/test_ingest_export_reader.py -v`
- Зависит от: —  (но требует `pip install ijson` — правило `ask`)

### Т3 — нормализация в БД, идемпотентно
- Файлы: `src/segregator/ingest/normalize.py`, `tests/test_ingest_backfill.py`
- Acceptance: `RawMessage` → строки `messages` / `blobs` / `attachments`;
  идемпотентность по `(chat_id, message_id)` и `(message_id, idx)`; один файл
  в двух сообщениях = 1 блоб + 2 `attachments`; возвращает счётчики
  (добавлено/пропущено/дубликатов).
- Verify: `python -m pytest tests/test_ingest_backfill.py -v`
- Зависит от: Т1, Т2

### Т4 — CLI `segregator backfill`
- Файлы: `src/segregator/cli.py`, `tests/test_cli_backfill.py`
- Acceptance: `--dry-run` считает и ничего не пишет; `--limit N` ограничивает;
  отчёт по-русски; повторный прогон печатает нули; логируются только счётчики,
  без имён файлов и содержимого.
- Verify: `python -m pytest tests/ -v` (полный прогон, 17 старых + новые)
- Зависит от: Т3

### Т5 — HTML-экспорт: читатель + выбор формата
Реальный экспорт канала — в HTML (`messages*.html`), не JSON. Читатель
изолирован, `normalize`/`blobs`/`cli` не меняются.
- Файлы: `src/segregator/ingest/html_reader.py`, `src/segregator/ingest/export.py`
  (новые), `src/segregator/ingest/normalize.py` (одна строка импорта),
  `pyproject.toml` (+`beautifulsoup4`), `tests/fixtures/export_html/`,
  `tests/fixtures/manifest.json`, `tests/test_ingest_html_reader.py`,
  `tests/test_ingest_export_dispatch.py`, `tests/test_ingest_backfill_html.py`
- Acceptance: `SPEC.md` Success Criteria 1–8 доказаны и на `export_html/`;
  `ingest.export` выбирает читатель по каталогу; JSON-путь и все прежние
  тесты Э2 зелёные без правок ассертов.
- Verify: `python -m pytest tests/ -v`
- Зависит от: Т3 (переиспользует `normalize`, `blobs`)

## Что вне плана

Э3+ (OCR, поля, классификация, раскладка). Реальный прогон по настоящему
экспорту — действие пользователя после переписи и выбора `ARCHIVE_DIR`.
