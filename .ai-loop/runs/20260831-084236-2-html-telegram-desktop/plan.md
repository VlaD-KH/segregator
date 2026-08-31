# План BUILD — подключить HTML-читатель к backfill

Спека: `.ai-loop/runs/20260831-084236-2-html-telegram-desktop/spec.md`.
Тесты (R3, R4, A1–A7) пишутся в VERIFY, не здесь.

## Шаги

### Шаг 1 — документы к HTML-реальности (R5)
- Файлы: `SPEC.md`, `tasks/plan.md`
- Что: снять Open Question про формат; приёмка на обеих фикстурах; диаграмма
  зависимостей — читатель за слоем выбора; добавить задачу Т5.
- Зона: E. Ожидаемый гейт: allow (или require_human из-за кумулятивного
  диффа прошлой партии — не из-за этого шага).

### Шаг 2 — слой выбора формата + провод в normalize (R1, R2)
- Файлы: `src/segregator/ingest/export.py` (новый), `pyproject.toml`
  (+`beautifulsoup4`), `src/segregator/ingest/normalize.py` (импорт из
  `.export` вместо `.export_reader`)
- Что: `export.py` детектит формат по каталогу (`result.json` → JSON;
  `messages*.html` в корне → HTML; иначе `FileNotFoundError` про оба),
  реэкспортит `iter_messages` / `read_chat_id`. `normalize.py` меняет одну
  строку импорта. `export_reader.py` (зона R) не трогается. `cli.py`
  (зона I) не трогается — текст ошибки уже общий.
- Зона: E (`ingest/`) + I (`pyproject.toml`). Ожидаемый гейт: **require_human**
  (новая зависимость + зона I + кумулятив). Закрывается `/ai-approve`.

## Замечено по дороге — НЕ трогать в этом прогоне
(переносится в `notes.md`)
