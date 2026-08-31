# Spec — Волна 1 (R-1..R-4)

Полная спецификация с критериями приёмки, таблицей «как пройти и остаться неверным»,
контрактом API и обоснованием границ (Принцип 15, `merge_authority: human`) уже написана
и утверждена ExitPlanMode в этой сессии: см.
`C:/Users/Huawei/.claude/plans/c-users-huawei-source-why-ai-docs-docs-enchanted-oasis.md`,
раздел «Волна 1». Не дублируется здесь — тот документ авторитетен.

Сжато, для ledger:

- **R-1**: смержить `agent/sonnet5/telegram-bridge` (11 впереди / 9 позади main) в main.
- **R-2**: `Core/server.py` — реальные `verified_tests`/`active_invariants`, авторизация
  `POST /api/panic`, удалить orphan `Eye/telegram_mini_app.html`.
- **R-3**: `Tool/connectors/llm_connector.py` — новый stdlib-коннектор к Anthropic Messages
  API, паттерн `telegram_connector.py`. Живая проверка отложена (нет `ANTHROPIC_API_KEY`).
- **R-4/R-4b**: `Supervisor/CommitGate.py` (реальный apply/commit/test/publish в
  `self-evo/<cycle_id>`, не в main), `Supervisor/EvolutionDaemon.py` (try/finally, ledger,
  HEAD-инвариант, фикс невалидного `candidate_diff`), `Supervisor/QuorumReviewer.py:42`
  (casefold).

Каждый — отдельная ветка/PR, зона R, `require_human` по политике независимо от объёма.
