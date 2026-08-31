# Spec — контур и быстрые дефекты перед первым реальным прогоном Э2

| | |
|---|---|
| Run id | `20260831-145622-2-docs-functional-checklist-md-1` |
| Source idea | `Контур и быстрые дефекты перед первым реальным прогоном Э2. Из docs/FUNCTIONAL_CHECKLIST.md: (1a) объявить pytest в pyproject.toml как optional-dependency dev; (3) D2 — текст исключений в ingest/html_reader.py и ingest/export_reader.py не должен содержать содержимого экспорта, только id и причину; (4) D1 — html_reader не декодирует percent-encoded href (unquote); подготовить (не применять) текст правок зоны P: deny-правило ./JDG/** и **/.env, актуализация EXPORT_DIR, CI-workflow. Наблюдаемость и Э6 — отдельными прогонами.` |
| Author | agent (Claude Code) + vlad |
| Status | draft |

## 1. Problem

`docs/FUNCTIONAL_CHECKLIST.md` фиксирует 17 дефектов, найденных разбором кода
до первого реального прогона Э2. Этот прогон закрывает три самых дешёвых и
один инфраструктурный, плюс готовит (не применяет) правки, которые может
сделать только владелец.

- **1a.** `pyproject.toml` не объявляет `pytest`. `git clone` + `pip install -e .`
  на чистой машине → `No module named pytest`. Проверено фактически в SHIP
  прошлого прогона: пришлось `pip install pytest` руками. SPEC пишет «pytest
  9.0» — а поставить его нечем.
- **D2 (checklist).** Текст исключений печатает содержимое экспорта:
  - `html_reader.py:152` — `raise ValueError(f"Не разобрана дата сообщения: {title!r}")`
    (`title` = сырая строка даты из атрибута сообщения);
  - `html_reader.py:191` — `raise ExportPathError(f"... {relative!r}")` (`relative` = href = **имя файла вложения**);
  - `export_reader.py:123` — то же самое (`relative` = путь вложения из `file`);
  - `blobs.py:49` — `raise FileNotFoundError(f"Файл вложения не найден: {source}")`
    (`source` = полный путь к конкретному документу) — **тот же класс, тот же
    one-liner**, поэтому в область включён, хотя в идее назван не был.

  Ни один обработчик эти исключения не гасит: `normalize.backfill` и
  `cli.backfill` ловят только по типу, текст уходит в stderr / stdout как есть.
  `cli.py:81-85` печатает `f"Экспорт отклонён: {error}"` — имя файла на экран.
  Нарушение `docs/DATA_BOUNDARY.md`, инвариант 4 (в лог и вывод — идентификаторы
  и счётчики, не содержимое). Рвётся ровно в момент отладки, когда traceback
  копируют в переписку.
- **D1 (checklist).** `html_reader._resolve_inside` получает `href` как есть,
  без `urllib.parse.unquote`. Telegram Desktop процентно-кодирует href для
  имён с пробелами и не-ASCII (`files/faktura%20nr%201.pdf`, польская
  диакритика). Резолвится путь с литеральным `%20`, `resolved.is_file()` →
  `False`, вложение уходит в `missing_files` **тихо**. На реальном польском
  экспорте — десятки ложных «файл не найден», неотличимых от честно
  невыгруженного медиа.
- **Зона P (D15, D16, 1b).** `JDG/` переехал в рабочий каталог, `.claude/settings.json`
  запрещает `Downloads/**` — старый адрес. `.env.example` / `HANDOFF.md`
  указывают `EXPORT_DIR` на несуществующий путь. CI нет. Всё это — commits
  владельца в зонах P и I; цикл их не делает. Задача прогона — дать владельцу
  готовый текст.

## 2. Goals

1. Свежий клон собирает тестовое окружение одной командой и гоняет suite.
2. Ни одно исключение в `ingest/` не выносит в текст имя файла, путь к
   документу или содержимое сообщения — только идентификаторы и причину.
3. `html_reader` резолвит percent-encoded href; JSON-путь не затронут.
4. У владельца на руках — точные диффы правок зоны P, готовые к применению.

## 3. Non-goals

- **D3/D4** — превращать фатальный `raise` (плохая дата, отбитый путь) в
  счётчик с порогом. Это наблюдаемость (F-2.42), отдельный прогон. Здесь —
  только санитизация **текста** исключений, поведение (падает / не падает)
  не меняется.
- **Применять** правки в `.claude/settings.json`, `.env.example`, `HANDOFF.md`,
  `.github/`. Пишется proposal-документ, дальше — руки владельца.
- Остальные дефекты D5–D14, D17; наблюдаемость F-2.35…F-2.45; Э6.
- Переписывать `export_reader.py` по существу — трогается **только** строка
  текста исключения (зона R, минимальная правка, под ручное одобрение).
- Реальный прогон на настоящем экспорте.

## 4. Requirements

| # | Requirement | Priority | Zone (predicted) |
|---|---|---|---|
| R1 | `pyproject.toml` → `[project.optional-dependencies] dev = ["pytest>=9.0"]`; `pip install -e ".[dev]"` ставит pytest | P0 | I |
| R2 | `html_reader.py:191` и `export_reader.py:123` (`ExportPathError`): текст без `relative` — говорит «путь вложения вне корня экспорта» и всё | P0 | E + **R** |
| R3 | `html_reader.py:152` (`ValueError` про дату): текст без `title` — «не разобрана дата сообщения», плюс message_id как идентификатор | P0 | E |
| R4 | `blobs.py:49` (`FileNotFoundError`): без `source` — «файл вложения не найден на диске» | P0 | E |
| R5 | `html_reader`: `unquote(href)` перед `_resolve_inside`; percent-encoded имя резолвится в реальный файл | P0 | E |
| R6 | JSON-читатель поведением не меняется (Telegram JSON пишет литеральные пути, decode там не нужен и не добавляется) | P0 | E |
| R7 | `.ai-loop/runs/<id>/zone-p-proposal.md` — готовые диффы: deny `./JDG/**`, `./ChatExport_*/**`, `**/.env` в `.claude/settings.json`; `EXPORT_DIR` в `.env.example` и `HANDOFF.md`; `.github/workflows/ci.yml` (pytest на push/PR) | P1 | (runs/ — вне классификации) |
| R8 | Ничего под `.claude/`, `.github/`, `.env.example`, `HANDOFF.md` в диффе прогона нет | P0 | — |

Порядок BUILD: R1 → R5 → (R2, R3, R4) → R7.

## 5. Acceptance criteria

### A1 (R1) — свежий клон собирает тесты одной командой

```
Given  git clone репозитория во временный каталог, свежий venv
When   pip install -e ".[dev]"  затем  python -m pytest -q
Then   установка проходит без ошибок, pytest импортируется,
       suite запускается (число passed == актуальному в main на момент прогона,
       зафиксировать фактическое, не предсказывать)
```

### A2 (R2, R3, R4) — исключения не несут данных экспорта

```
Given  синтетический экспорт, где href вложения = "../../../evil.pdf"
When   iter_messages_html / iter_messages доходит до него
Then   поднимается ExportPathError
  и    str(исключения) НЕ содержит подстроку "evil" и подстроку "../"
  и    str(исключения) содержит слова про "корень экспорта"

Given  синтетическое HTML-сообщение с датой "чушь-не-дата" в title
When   _sent_at разбирает его
Then   поднимается ValueError
  и    str(исключения) НЕ содержит "чушь-не-дата"
  и    str(исключения) содержит "дата" и номер сообщения

Given  вызов store_blob / sha256_of на несуществующем пути
       .../files/tajna-faktura-kowalski.pdf
When   файл не найден
Then   str(исключения) НЕ содержит "tajna-faktura-kowalski"
```

Дополнительно (grep как быстрый инвариант): в `src/segregator/ingest/*.py`
ни один `raise` не интерполирует в текст переменные `relative`, `title`,
`source`, `href`, `item.get('text')`, `body`.

### A3 (R5) — percent-encoded href резолвится

```
Given  синтетический HTML-экспорт: href = "files/faktura%20nr%201.pdf",
       на диске лежит files/faktura nr 1.pdf
When   iter_messages_html разбирает сообщение
Then   attachment.path == <export>/files/faktura nr 1.pdf
  и    attachment.exists is True
  и    attachment НЕ попадает в missing_files при backfill

Given  href = "files/%2e%2e%2f%2e%2e%2fevil.pdf" (percent-encoded ../..)
When   резолвится
Then   ExportPathError (decode не должен открывать дыру в защите от traversal)
```

### A4 (R6) — JSON-путь не тронут

```
Given  фикстура tests/fixtures/export_min/ (result.json, литеральные пути)
When   python -m pytest tests/test_ingest_export_reader.py tests/test_ingest_backfill.py
Then   все ранее зелёные тесты зелёные, ни один ассерт не правился
```

### A5 (R7, R8) — зона P подготовлена, не применена

```
Given  завершённый BUILD прогона
When   git diff --stat main..HEAD  и  git status
Then   в диффе нет .claude/**, .github/**, .env.example, HANDOFF.md
  и    .ai-loop/runs/<id>/zone-p-proposal.md существует и содержит
       конкретные диффы (не «надо бы добавить»), готовые к git apply или
       ручному переносу
```

## 6. How this could pass and still be wrong

- **A2 зелёный, потому что тест проверяет только один из путей утечки.**
  Защита: grep-инвариант по всем `raise` в `ingest/`, плюс отдельный триггер
  на каждое из 4 мест. VERIFY (falsification) обязан вернуть `{relative!r}` в
  одно из мест и убедиться, что тест это ловит.
- **D2 «починен» в `raise`, но то же значение уходит в `log.warning`
  рядом.** `normalize.py:127` логирует `message_id, idx` — id, не имя,
  это ок. Но проверить, что санитизация не оставила зеркальную утечку в
  логах: grep по `log.` в `ingest/` на те же переменные.
- **A3 проходит на `%20`, но `unquote` применён не там** — например к уже
  резолвленному пути, а не к сырому href, и на `%2f` (слэш) ломает защиту
  traversal. Защита: вторая ветка A3 с percent-encoded `../..`; VERIFY
  проверяет порядок (decode → resolve → is_relative_to), а не только результат.
- **`unquote` меняет поведение JSON-читателя**, если рефакторинг вынес
  `_resolve_inside` в общий модуль. Защита: R6 + A4; decode остаётся
  строго в `html_reader`.
- **A1 «passed N», где N подогнан.** Защита: фиксировать фактическое число
  из прогона на чистом клоне, в spec его не предсказывать; A1 сверяет с
  тем же прогоном на рабочей машине.
- **zone-p-proposal.md написан, но диффы не применяются чисто** (контекст
  разъехался). Защита: проверить `git apply --check` для тех, что можно
  (`.env.example`, `HANDOFF.md`); для `.claude/settings.json` и CI —
  показать полный целевой файл, а не только фрагмент.
- **Правка текста `ExportPathError` в `export_reader.py` (R) заодно
  трогает логику `is_relative_to`.** Защита: дифф этого файла обязан быть
  одной строкой в тексте `raise`, ничего больше; scope-reviewer в REVIEW.

## 7. Risk and rollback

**Зоны:** I (`pyproject.toml`), E (`ingest/html_reader.py`, `blobs.py`),
**R** (`ingest/export_reader.py` — одна строка текста исключения), T (новые
фикстуры + тесты). Кумулятивный дифф → `require_human` (зона I + зона R).

**Контур:** цель прогона — укрепить контур (убрать утечку в исключениях,
подготовить deny-правила). `boundary-guard` обязателен. Риск обратный
обычному: неверная санитизация может **спрятать** полезную для отладки
информацию (какой именно путь отбит) — компенсируется тем, что причина и
номер сообщения остаются.

**Если неверно в бою:** утечка в тексте исключения вернётся (косметика,
не потеря данных) либо percent-decode откроет дыру в traversal-защите
(серьёзно — ловится A3 второй веткой и REVIEW). Откат: `git revert` коммитов
прогона; `pyproject.toml` вернётся без `[dev]`, `html_reader` — без decode.

## 8. Open questions

Блокирующих BUILD нет.

- Формулировка санитизированных сообщений — на усмотрение BUILD, критерий
  один: без данных, но причина понятна. Если владелец хочет конкретный
  текст — скажет на гейте.
- `pytest>=9.0` или мягче (`>=8`)? Ставим `>=9.0` под SPEC Tech Stack;
  правится одной строкой, если это создаст проблему на других машинах.
