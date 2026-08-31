# TODO: Э2 «Бэкфилл»

Подробности и критерии — в `tasks/plan.md`. Спека — `SPEC.md`.

- [x] **Т1 — хранилище блобов**
  - Acceptance: `store_blob()` → `(sha256, path, was_new)`; повтор не создаёт
    второй файл; запись атомарна через `.part` + `replace()`
  - Verify: `python -m pytest tests/test_ingest_blobs.py -v` → 7 passed
  - Files: `src/segregator/ingest/blobs.py`, `tests/test_ingest_blobs.py`

- [x] **Т2 — потоковый читатель экспорта**
  - Acceptance: `iter_messages()` потоково через `ijson`; путь вне корня
    экспорта отвергается; отсутствующий файл не роняет итерацию; служебные
    сообщения пропускаются
  - Verify: `python -m pytest tests/test_ingest_export_reader.py -v` → 10 passed
  - Files: `src/segregator/ingest/export_reader.py`,
    `tests/fixtures/export_min/`, `tests/fixtures/manifest.json`,
    `tests/test_ingest_export_reader.py`, `pyproject.toml`
  - `ijson` 3.5.1 установлен с явного разрешения

- [x] **Т3 — нормализация в БД**
  - Acceptance: идемпотентность по естественным ключам; 1 файл в 2 сообщениях
    = 1 блоб + 2 `attachments`; возвращает счётчики
  - Verify: `python -m pytest tests/test_ingest_backfill.py -v` → 8 passed
  - Files: `src/segregator/ingest/normalize.py`, `tests/test_ingest_backfill.py`

- [x] **Т4 — CLI `segregator backfill`**
  - Acceptance: `--dry-run` ничего не пишет; `--limit N` работает; отчёт
    по-русски; повторный прогон печатает нули; в логах только счётчики
  - Verify: `python -m pytest tests/ -v` → 48 passed
  - Files: `src/segregator/cli.py`, `tests/test_cli_backfill.py`

## Найдено и починено по ходу

- **Сухой прогон врал про дедупликацию.** `--dry-run` показывал «новых 4»,
  боевой прогон — «новых 3, дублей 1»: файл, встреченный дважды, считался
  новым оба раза, потому что на диск ничего не пишется и коллидировать не с
  чем. Вскрылось на сквозном прогоне CLI, а не в юнит-тестах. Закрыто
  множеством `seen_digests` и тестом
  `test_dry_run_predicts_the_real_run_exactly`.
- **`probe/01_host_and_export.ps1` терял UTF-8 BOM** — без него PowerShell 5.1
  читает файл в системной кодировке (здесь cp1250) и ломается на кириллице.

## Гейты перед коммитом

- [x] Полный `pytest` зелёный, включая 17 тестов Э1 → 48 passed
- [x] `boundary-guard` — чистый вердикт (правило `CLAUDE.md`): сети в `ingest/`
      нет, в логи идут только счётчики и числовые id, фикстуры синтетические
      и зарегистрированы, path traversal и абсолютные пути отбиваются
- [x] `ijson` объявлен в `pyproject.toml` (иначе чистый `pip install -e .`
      не собрал бы пакет — поймано на ревью)
- [x] Коммит по задаче, `git status` чист
