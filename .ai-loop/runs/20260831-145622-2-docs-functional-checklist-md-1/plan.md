# План BUILD — контур и быстрые дефекты

Спека: `.ai-loop/runs/20260831-145622-2-docs-functional-checklist-md-1/spec.md`.
Формальные тесты (A1–A5) — в VERIFY. В BUILD: код + фикстуры + smoke руками.

## Проверенные допущения (Step 4)

- JSON-фикстура пишет литеральные пути (`"file": "files/doc_a.pdf"`) — percent-decode
  в `export_reader` не нужен, R6 держится.
- `unquote("%2e%2e%2f")` → `"../"`; порядок **decode → resolve → is_relative_to**
  безопасен, `resolve()` нормализует `..`, `is_relative_to` ловит выход. Проверено.
- `pip install -e ".[dev]"` — PEP 621 optional-dependencies, setuptools>=68 в
  `pyproject.toml` это поддерживает.

## Ожидаемый кумулятивный гейт

`require_human` начиная с S1 (зона I). Точно на S4 (зона R). Это плановые
точки остановки, не помеха.

## Шаги

### S1 — pytest как dev-зависимость  ·  зона I / HIGH
- Файл: `pyproject.toml`
- `[project.optional-dependencies]` → `dev = ["pytest>=9.0"]`
- Контрактная правка (метаданные сборки) — идёт первой, отдельным коммитом.
- Критерий: A1. Гейт: **require_human** (зона I).

### S2 — decode percent-encoded href  ·  зона E / LOW
- Файл: `src/segregator/ingest/html_reader.py`
- `from urllib.parse import unquote`; в `_resolve_inside` декодировать
  `relative` **до** резолва (сырой href из данных → `unquote` → резолв →
  проверка выхода за корень).
- **Амендмент 2026-08-31:** фикстурного файла НЕ будет. Добавление сообщения
  в общую `tests/fixtures/export_html/` сдвигает счётчики в уже закоммиченном
  `test_ingest_html_reader.py` (`ids == [101..107]`, `len == 7`). Тест D1 в
  VERIFY строится инлайн через хелпер `_export_with`, который уже есть в
  файле тестов для traversal-проверок.
- Критерий: A3. Поведение JSON-читателя не трогается (R6).

### S3 — санитизация текста исключений (E)  ·  зона E / LOW
- Файлы: `src/segregator/ingest/html_reader.py`, `src/segregator/ingest/blobs.py`
- `html_reader.py:152` — `ValueError` про дату: убрать `{title!r}`, оставить
  «не разобрана дата сообщения» + `message_id` (идентификатор).
- `html_reader.py:191` — `ExportPathError`: убрать `{relative!r}`, оставить
  «путь вложения ведёт за пределы корня экспорта».
- `blobs.py:49` — `FileNotFoundError`: убрать `{source}`, оставить «файл
  вложения не найден на диске».
- Один класс правки («текст исключения без данных») → один коммit. Проверить
  grep-ом, что рядом нет зеркальной утечки в `log.*`.
- Критерий: A2 (кроме export_reader).

### S4 — санитизация ExportPathError в export_reader  ·  зона R / CRITICAL
- Файл: `src/segregator/ingest/export_reader.py`
- **Ровно одна строка**: `f"... {relative!r}"` → текст без `relative`.
  Логика `is_relative_to` не трогается. Дифф обязан быть однострочным.
- Отдельный шаг именно чтобы ручное одобрение зоны R было тривиальным:
  «это только текст? да → ок».
- Критерий: A2 (ветка export_reader). Гейт: **require_human** (зона R).

### S5 — proposal зоны P для владельца  ·  вне классификации
- Файл: `.ai-loop/runs/<id>/zone-p-proposal.md` (`.ai-loop/runs/` исключён
  из классификатора)
- Готовые диффы, не пожелания:
  - `.claude/settings.json` — в `deny`: `Read(./JDG/**)`, `Read(./ChatExport_*/**)`,
    `Read(./**/.env)`; показать целевой файл целиком;
  - `.env.example` — `EXPORT_DIR` на актуальный путь (репозиторный `JDG/` или
    то, что скажет владелец), с `git apply --check`;
  - `HANDOFF.md` — тот же путь;
  - `.github/workflows/ci.yml` — полный файл: `pip install -e ".[dev]"`,
    `python -m pytest -q`, на `push` и `pull_request`.
- Критерий: A5. Ничего под `.claude/` / `.github/` / `.env.example` / `HANDOFF.md`
  в диффе прогона не появляется.

## Амендмент 2026-08-31 — S2 и S3 сливаются в один коммит

S2 (`unquote`) и S3 (санитизация текста) правят **один файл**
`html_reader.py` разными хунками. Разделить их на два коммита можно только
хирургией по патчу (`git apply --cached` с отфильтрованным диффом) — риск
сломать дерево выше пользы. Коммитим вместе: два ясных хунка в одном файле,
ревью не страдает. Изолированным остаётся то, ради чего изоляция и нужна —
`export_reader.py` (зона R, одна строка).

Итоговые коммиты: C1 `pyproject.toml` · C2 `html_reader.py` + `blobs.py` ·
C3 `export_reader.py` (R) · C4 артефакты прогона.

## Порядок и чистота

S1 (контракт) → S2 (поведение) → S3 (косметика E) → S4 (косметика R) → S5 (доки).
После любого шага репозиторий в состоянии, из которого можно уйти. S4 не делает
S5 обязательным и наоборот.

## Параллелизм

BUILD последовательный: S2/S3 трогают один файл (`html_reader.py`), два агента
на нём — конфликт слияния. Реальная параллель у цикла — в REVIEW
(adversarial + scope + falsification одновременно, все read-only). Туда и
пущу агентов.
