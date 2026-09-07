# Как запустить

## Установка

Нужен **Python 3.12 или новее**. На 3.11 установка падает с
`requires a different Python: 3.11.x not in '>=3.12'`.

```powershell
# Windows
cd prospektor
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev,xlsx,web]"
.venv\Scripts\pytest -q
```

```bash
# Linux / macOS
cd prospektor
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,xlsx,web]'
.venv/bin/pytest -q
```

Тесты работают полностью офлайн и не требуют сети. Способ вызова значения не
имеет: `pytest`, `python -m pytest` и запуск из корня репозитория дают
одинаковый результат.

## Перед первым прогоном

Создайте `prospektor/.env` и представьтесь чужим сайтам — значение по умолчанию
это заглушка:

```
PROSPEKTOR_USER_AGENT=prospektor/0.1 (audit; kontakt: you@example.com)
```

Прочие полезные переменные — в README, раздел «Настройки». Обратите внимание на
`PROSPEKTOR_BUDGET_USD`: по умолчанию ноль, то есть платные источники запрещены
и случайный прогон не выставит счёт.

## Прогон

```bash
prospektor discover --area "Szczecin" --profile beauty_pl
prospektor enrich
prospektor audit --profile beauty_pl --limit 200
prospektor score --profile beauty_pl
prospektor query beauty-tylko-platforma --profile beauty_pl --export xlsx
prospektor serve                      # дашборд на http://127.0.0.1:8080
```

Что делает каждая стадия и куда ходит — в README.

## Что стоит знать

**`audit` — единственная стадия, которая ходит на чужие сайты.** Кандидаты
отбираются автоматически: сети, поштоматы, банкоматы и школы отсеиваются до
первого запроса, и команда печатает, сколько и почему отсеяно. Посмотреть
поимённо — `--explain`. Начинайте с `--limit`, чтобы оценить объём.

**Смотрите на колонку `coverage` в выгрузке.** Это доля правил профиля, которые
удалось измерить. Пока аудит не прошёл, измеряется только то, что видно из
адреса, — около 10%, и «разрыв 100» при таком покрытии предварительный.

**`web.has_site = false` означает именно отсутствие сайта**, а не сбой связи:
таймаут, 403 и обрыв соединения дают `web.check_inconclusive`, и правило
скоринга такой сигнал пропускает.

**Профиль — это линза на вертикаль.** `score --profile beauty_pl` оценивает
только салоны; чтобы посчитать весь город целиком, нужен явный флаг в коде
(`score_all(..., whole_city=True)`).

## Если Overture недоступен

`discover` использует DuckDB с расширением `httpfs`. В окружении, где
`extensions.duckdb.org` закрыт, но S3-бакет Overture доступен, parquet читается
HTTP range-запросами: 29–31 МБ на город, отсечение по статистике row groups.
Так снимались данные Старгарда и Щецина — см. `docs/RUN-SZCZECIN.md`.
