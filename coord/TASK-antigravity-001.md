# Задание 001 для Antigravity — `accounting/period.py`

Читается вместе с `coord/ANTIGRAVITY.md` (постоянная инструкция). Здесь — только
то, что относится к этой задаче.

---

## Где работать

```
Каталог: C:\Users\Huawei\source\segregator-period
Ветка:   feat/period-close          (уже создана, уже выкачена — checkout не нужен)
База:    fix/merge-readiness @ dd654fa
```

Это **отдельное рабочее дерево**, заведённое под эту задачу. Claude Code
работает в `segregator-fix`, ты — здесь. Пересечений по файлам нет: ты создаёшь
один новый модуль, он не трогает ни его, ни `service.py`, ни миграции.

Ветка `feat/agent-business-logic` и каталог `segregator-agents` для этой задачи
**не используются** — они остались от прошлого захода.

---

## Что сделать

Создать **один файл**: `src/segregator/accounting/period.py`.

Ничего больше не трогать. Ни `service.py`, ни `orchestrator/`, ни миграции, ни
`pyproject.toml`. Проводку этого модуля в конвейер делает Claude Code — так вы
не столкнётесь на общих файлах.

### Контракт

```python
next_lp(conn: sqlite3.Connection, entry_date: date) -> int
```
Следующая *liczba porządkowa* для книги того года, в который попадает
`entry_date`. Сквозная в пределах года, с нового года — снова с 1.
Основывается на `MAX(lp)`, а не на количестве строк: после сторно в середине
книги нумерация обязана продолжаться, а не переиспользовать освободившийся номер.

```python
@dataclass(frozen=True)
class PeriodTotals:
    period: str        # "YYYY-MM"
    przychody: Decimal
    koszty: Decimal
    dochod: Decimal    # przychody - koszty, может быть отрицательным
    entries: int
```

```python
close_month(conn: sqlite3.Connection, period: str) -> PeriodTotals
```
Агрегат по всем проводкам месяца: `przychody` — сумма `col_9_razem_przychody`,
`koszty` — сумма `col_14_razem_wydatki`. Период задаётся строкой `"YYYY-MM"`.

Обязательное поведение:
- **месяц без проводок → `ValueError`**, а не нули. Нулевой доход и отсутствие
  данных — разные вещи: первое значит, что предприниматель не заработал,
  второе — что документы ещё не обработаны. Нули отдали бы декларацию за
  необработанный месяц;
- **неверный формат периода → `ValueError`**;
- деньги наружу выходят как `Decimal`. В БД колонки объявлены `REAL`, то есть
  читаются как `float` — приводить через `str()`, не `Decimal(float)`, иначе
  `0.1 + 0.2` даст `0.30000000000000004`.

### Схема таблицы

`kpir_entries` из `src/segregator/db/migrations/0002_business_ledger.sql`.
Нужные колонки: `lp INTEGER`, `entry_date TEXT` (ISO `YYYY-MM-DD`),
`col_9_razem_przychody REAL`, `col_14_razem_wydatki REAL`.

Колонки `taxpayer_nip` в этой таблице **нет** — система однопользовательская
(`SPEC.md`: «Пользователь один — владелец JDG»), фильтровать по плательщику
не нужно и нечем.

---

## Готово — это когда

```bash
cd C:\Users\Huawei\source\segregator-period
python -m pytest tests/test_accounting_period.py -q
```

даёт **11 passed**, и

```bash
python -m pytest tests/ -q
```

даёт **184 passed** (173 существующих + 11 твоих).

Тесты уже написаны и лежат в `tests/test_accounting_period.py`. Сейчас они
красные: `ModuleNotFoundError: No module named 'segregator.accounting.period'`.
Это и есть задание — сделать их зелёными.

**Тесты не менять.** Они контракт, а не черновик. Считаешь тест неверным —
`question` на доску, не правка ассерта. Если реализация не проходит тест, чинить
надо реализацию.

Отчёт — дословный вывод обеих команд, по §3 постоянной инструкции.

---

## Зачем это нужно (чтобы понимать, а не угадывать)

Три дефекта, которые закрывает твой модуль:

1. `orchestrator/nodes.py:181` ставит `lp=1` каждой строке. Колонка 1 книги —
   *liczba porządkowa* по закону; книга с тремя строками под номером 1 книгой
   не является. Реестр XLSX сортируется `ORDER BY lp` и выходит в произвольном
   порядке.
2. `orchestrator/nodes.py:209` подставляет `monthly_profit = Decimal('10000.00')` —
   захардкоженную заглушку, если у документа нет выручки. Реальные 12 000
   выручки и 1836.25 расходов в декларацию не попадают.
3. `service.py:316,346` пишет месячные цифры через `INSERT OR REPLACE`, поэтому
   последний обработанный документ затирает весь месяц. Перестановка файлов в
   папке меняет объявленный медицинский взнос.

Твой модуль даёт источник правды для всех трёх: цифры считаются из проводок за
период, а не из одного документа.

---

## Протокол доски

В начале:
```bash
python coord/board.py claims
python coord/board.py post --from antigravity --kind claim \
  --paths src/segregator/accounting/period.py \
  --subject "Взял TASK-001" --refs blocker-3 blocker-4
```

В конце:
```bash
python coord/board.py post --from antigravity --kind release \
  --paths src/segregator/accounting/period.py \
  --subject "TASK-001 готов, 184 passed" --body "<дословный вывод pytest>"
```

Вопрос по контракту — `question` с `--to claude`, и жди ответа, не додумывай.

---

## Коммит

```bash
git add src/segregator/accounting/period.py
git commit -m "feat(accounting): агрегаты периода и сквозная нумерация KPiR"
```

Только этот путь. `git add -A` — нет (§1 постоянной инструкции).
`tests/test_accounting_period.py` уже закоммичен, добавлять его не нужно.
