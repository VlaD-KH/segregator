# Переезд в Claude Code

Всё, что нужно, уже лежит в этой папке. Облачная сессия дальше не обязательна.

## Что здесь есть

```
CLAUDE.md              контекст проекта — Claude Code читает его автоматически
.claude/settings.json  запреты: экспорт, архив, блобы, БД, логи, curl/wget
.claude/agents/        четыре субагента разработки
agents/                спецификации четвёрки доменных агентов (локальная модель)
agents/schemas/        JSON-контракты между агентами
docs/                  архитектура и контур данных
probe/                 разведка железа и экспорта
```

## Порядок запуска

1. Прогнать разведку, если ещё не прогнал:
   `powershell -ExecutionPolicy Bypass -File probe\01_host_and_export.ps1`
   Отчёт ляжет в `probe\probe_result.txt`. Прочитать глазами.

2. Поднять локальную модель. После probe будет понятно — Ollama или llama.cpp
   и какой квант. Оба дают OpenAI-совместимый эндпоинт на localhost, код от
   выбора не зависит: адрес и имя модели живут в `.env`.

3. Поставить Claude Code и запустить его в этой папке.

   PowerShell:
   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```
   или WinGet: `winget install Anthropic.ClaudeCode`.
   Полезно доставить Git for Windows — тогда у Claude Code будет Bash,
   а не только PowerShell. Проверка: `claude --version`, диагностика: `claude doctor`.
   Затем из папки проекта:
   ```powershell
   cd C:\Users\Huawei\source\segregator
   claude
   ```

4. Первое, что сказать Claude Code:

   > Прочитай CLAUDE.md, docs/ARCHITECTURE.md и docs/DATA_BOUNDARY.md.
   > Затем прочитай probe/probe_result.txt и предложи модель и квантизацию
   > под это железо. После этого — этап Э1 из ТЗ: скелет проекта, конфиг,
   > схема БД и миграции, CLI, каркас тестов.

5. Дальше по этапам Э1…Э9 из ТЗ. Перед каждым коммитом, который трогает
   `src/`, `agents/` или `tests/`, гонять субагента `boundary-guard`.

## Чего Claude Code делать не должен

- Читать папку экспорта, `archiwum/`, `blobs/`, `*.db`, `logs/`, `.env`.
  Запрещено в `.claude/settings.json`, но знай это и сам.
- Класть настоящие документы в `tests/fixtures/`. Только синтетика с маркером.
- Молча править налоговые константы. Только через `pl-tax-checker` со ссылкой.
- Подавать что-либо в KSeF, ZUS или e-Deklaracje. Система готовит файл,
  отправляет человек.

## Переменные окружения (`.env` рядом с этим файлом, права только владельцу)

```
TELEGRAM_BOT_TOKEN=...
OWNER_USER_ID=...
LLM_BACKEND=ollama            # или llama.cpp
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=...                 # решаем после probe
EXPORT_DIR=C:\Users\Huawei\Downloads\Telegram Desktop\JDG
ARCHIVE_DIR=...               # выбрать диск с запасом места
KSEF_ENABLED=false            # включим отдельно, вместе с токеном KSeF
```
