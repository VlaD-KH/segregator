# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).
Проект до первого релиза, всё живёт в `[Unreleased]`.

## [Unreleased]

### Добавлено

- **Читатель HTML-экспорта Telegram Desktop** (`src/segregator/ingest/html_reader.py`).
  Реальный экспорт канала «бухгалтерия» сделан в HTML (`messages.html` +
  пагинация), а не в `result.json`. Парсер на `beautifulsoup4` отдаёт те же
  `RawMessage` / `RawAttachment`, что и JSON-читатель.
- **Слой выбора формата экспорта** (`src/segregator/ingest/export.py`).
  `result.json` есть → JSON-читатель; иначе `messages*.html` в корне →
  HTML-читатель; иначе `FileNotFoundError` с упоминанием обоих форматов.
  `normalize.py` про формат источника больше не знает.
- **Разведчик структуры HTML** (`probe/02_html_structure.py`) — обезличенный
  отчёт (текст → длина, путь → каталог+расширение, дата → маска).
- **`docs/FUNCTIONAL_CHECKLIST.md`** — карта функционала Э1–Э7 с индексом
  отказов «симптом → вероятная причина» и списком из 17 дефектов (D1–D17),
  найденных разбором кода до первого реального прогона.
- Зависимость `beautifulsoup4>=4.12` (бэкенд `html.parser`, без `lxml`).

### Изменено

- `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` — приёмка Э2 теперь проверяется
  на обеих фикстурах (`export_min` JSON + `export_html` HTML). Открытый вопрос
  «формат экспорта не подтверждён» снят.
- `.gitignore` — правило `messages*.html` (под настоящий экспорт) больше не
  проглатывает синтетические страницы фикстуры
  (`!tests/fixtures/**/messages*.html`).

### Известные пробелы

- Э2 не проверялся фактическим прогоном на реальном экспорте. VERIFY этого
  прогона по решению владельца заменён на будущую проверку через Э6.
- `pytest` не объявлен в `pyproject.toml` — свежий клон не соберёт тестовое
  окружение одной командой.
- CI отсутствует (`.github/workflows/`), тесты на push не гоняются.
- Дефекты D1–D17 из `docs/FUNCTIONAL_CHECKLIST.md` не устранены.
