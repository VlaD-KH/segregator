# Правки зоны P — применяет владелец

Цикл эти файлы не трогает (`.claude/`, `.github/`, `.env.example`, `HANDOFF.md`,
`CLAUDE.md` — контурная формулировка). Ниже готовый текст. Порядок применения:
скопировать, проверить, закоммитить своей рукой — как было с `.gitignore` в
прошлом прогоне.

---

## 1. `.claude/settings.json` — закрыть экспорт от чтения агентом (D15)

**Проблема.** Настоящий экспорт переехал из `Downloads\Telegram Desktop\JDG`
в `C:\Users\Huawei\source\segregator\JDG\` (внутри рабочего каталога).
Правило `Read(//C:/Users/Huawei/Downloads/**)` бьёт по старому адресу.
`JDG/` сейчас не в `deny` и не в `allow` — то есть формально агенту не
запрещён. Внутри `JDG/` лежит `.env` с токеном бота; правило `Read(./.env)`
ловит только корневой файл.

**Целевой файл целиком:**

```json
{
  "permissions": {
    "deny": [
      "Read(//C:/Users/Huawei/Downloads/**)",
      "Read(./JDG/**)",
      "Read(./ChatExport_*/**)",
      "Read(./archiwum/**)",
      "Read(./blobs/**)",
      "Read(./rejestry/**)",
      "Read(./logs/**)",
      "Read(./*.db)",
      "Read(./.env)",
      "Read(./**/.env)",
      "Bash(curl:*)",
      "Bash(wget:*)"
    ],
    "ask": [
      "Bash(git push:*)",
      "Bash(pip install:*)",
      "Bash(ollama pull:*)"
    ],
    "allow": [
      "Read(./src/**)",
      "Read(./agents/**)",
      "Read(./rules/**)",
      "Read(./schemas/**)",
      "Read(./tests/**)",
      "Read(./docs/**)",
      "Edit(./src/**)",
      "Edit(./agents/**)",
      "Edit(./rules/**)",
      "Edit(./tests/**)",
      "Bash(pytest:*)",
      "Bash(python -m pytest:*)"
    ]
  }
}
```

Изменения: `+Read(./JDG/**)`, `+Read(./ChatExport_*/**)`, `+Read(./**/.env)`.

**Остаточный риск (вне этого прогона).** `deny` закрывает инструмент `Read`,
но не `Bash(cat:*)` / `Bash(type:*)` / `python -c "open(...)"`. Полностью
контур это не держит; для машины с одним пользователем-владельцем считаем
достаточным + `boundary-guard` на каждом коммите. Ужесточение — отдельный
разговор.

---

## 2. `.env.example` — актуальный `EXPORT_DIR` (D16)

Данные лежат в `C:\Users\Huawei\source\segregator\JDG`. `git apply` этого
диффа проверен (`git apply --check` ниже):

```diff
--- a/.env.example
+++ b/.env.example
@@
-EXPORT_DIR=C:\Users\Huawei\Downloads\Telegram Desktop\JDG
+# Экспорт Telegram Desktop. Данные перенесены внутрь репозитория и закрыты
+# .gitignore (JDG/) и .claude/settings.json (deny Read).
+EXPORT_DIR=C:\Users\Huawei\source\segregator\JDG
```

**И не забыть настоящий `.env`** (в git его нет, правит владелец):
поменять там `EXPORT_DIR` на тот же путь — именно старое значение в `.env`
давало исходную ошибку «Не найден result.json в …\Downloads\…».

---

## 3. `HANDOFF.md:69` — тот же путь (D16)

```diff
--- a/HANDOFF.md
+++ b/HANDOFF.md
@@
-EXPORT_DIR=C:\Users\Huawei\Downloads\Telegram Desktop\JDG
+EXPORT_DIR=C:\Users\Huawei\source\segregator\JDG
```

`HANDOFF.md` формально зона E, но идёт в этот же коммит владельца, чтобы не
плодить полу-правки контура.

---

## 4. `CLAUDE.md:15` — формулировка запрета по актуальному адресу (D16)

```diff
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@
-Claude Code **никогда** не читает `C:\Users\Huawei\Downloads\Telegram Desktop\JDG`,
-`archiwum/`, `blobs/`, `*.db`, `logs/`. Запреты продублированы в `.claude/settings.json`.
+Claude Code **никогда** не читает `JDG/`, `ChatExport_*/`, `archiwum/`, `blobs/`,
+`*.db`, `logs/`, `.env`. Запреты продублированы в `.claude/settings.json`.
```

---

## 5. `.github/workflows/ci.yml` — тесты на push (1b)

**Новый файл.** Прогоняет тот же `pytest`, что и локально, на целевой ОС
(проект Windows-only: iGPU, cp1250-локаль, были Windows-специфичные баги —
BOM в probe-скрипте, кодировка консоли).

```yaml
name: tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  pytest:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: install
        run: pip install -e ".[dev]"
      - name: pytest
        run: python -m pytest -q
```

Требует шага S1 этого прогона (`[project.optional-dependencies] dev` в
`pyproject.toml`) — без него `pip install -e ".[dev]"` не найдёт pytest.

**Опционально:** добавить `ubuntu-latest` в матрицу для скорости — тесты
кросс-платформенные (`tmp_path`/`monkeypatch`), Windows-специфику мокают.
Но основной прогон должен остаться на `windows-latest`.

---

## Проверка после применения

```powershell
cd C:\Users\Huawei\source\segregator
git apply --check <(git diff)   # для .env.example / HANDOFF.md / CLAUDE.md
python -c "import json; json.load(open('.claude/settings.json'))"   # валидный JSON
git add .claude/settings.json .env.example HANDOFF.md CLAUDE.md .github/workflows/ci.yml
git commit -m "контур: закрыть JDG/ от агента, актуализировать EXPORT_DIR, CI"
git push origin main
```

CI-workflow сработает на этом же push — первый зелёный чек и будет
подтверждением, что `[dev]`-зависимость и тесты собираются в чистом
окружении GitHub.
