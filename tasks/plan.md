# План: Э2 «Бэкфилл»

Источник требований — `SPEC.md` (раздел Success Criteria). Задачи нарезаны
вертикально: каждая — рабочий путь целиком со своим тестом, а не слой.

## Компоненты и зависимости

```
config.Settings ─┐
db.get_connection├─► ingest.normalize ◄── ingest.export_reader ◄── result.json
paths.ensure_tree┘         │                      (ijson, потоково)
                           ▼
                    ingest.blobs (sha256, content-addressed)
                           │
                           ▼
                    cli.backfill (--dry-run, --limit)
```

Порядок обязателен: `blobs` не зависит ни от чего, `export_reader` не зависит
от БД, `normalize` связывает оба, CLI — сверху. Поэтому Т1 и Т2 независимы и
могли бы идти параллельно; Т3 требует обеих; Т4 требует Т3.

## Риски и что с ними делаем

| Риск | Митигация |
|---|---|
| Формат экспорта окажется HTML, а не JSON | Разработка идёт против синтетической фикстуры; читатель изолирован в одном модуле, замена парсера не трогает normalize/blobs |
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

## Что вне плана

Э3+ (OCR, поля, классификация, раскладка). Реальный прогон по настоящему
экспорту — действие пользователя после переписи и выбора `ARCHIVE_DIR`.
