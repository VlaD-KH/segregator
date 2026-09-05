# Задание 004 для Antigravity — `route/tree.py`

Читается вместе с `coord/ANTIGRAVITY.md` (постоянная инструкция). Здесь — только
то, что относится к этой задаче.

**Зависит от TASK-002:** `tree.py` импортирует `safe_filename` из
`route/naming.py`. Делать после него.

---

## Где работать

```
Каталог: C:\Users\Huawei\source\segregator-route
Ветка:   feat/route-tree           (уже создана, уже выкачена — checkout не нужен)
База:    fix/merge-readiness
```

То же дерево, что и TASK-002/003. Claude Code в `route/` не заходит.

---

## Что сделать

Создать **один файл**: `src/segregator/route/tree.py`.

Ничего больше не трогать — в том числе `paths.py` и `service.py`. Константу
`NO_PAYMENT_DATE_DIR` **импортировать** из `segregator.paths`, а не объявлять
заново: второе объявление той же строки рано или поздно разъедется с первым.

### Контракт

```python
def month_folder(month: int) -> str
```

Польские имена месяцев с числовым префиксом — без префикса `ls` даёт
алфавитный порядок, и `sierpien` оказывается перед `styczen`:

```
01-styczen  02-luty      03-marzec   04-kwiecien
05-maj      06-czerwiec  07-lipiec   08-sierpien
09-wrzesien 10-pazdziernik 11-listopad 12-grudzien
```

Значение вне `1..12` → `ValueError`. Сейчас `MonthNames.get_month_folder`
(`service.py:56`) молча отдаёт `13-miesiac`; такой папки быть не должно.

```python
def document_tree_path(archive_dir: Path, doc_date: date, category: str, filename: str) -> Path
def payment_tree_path(archive_dir: Path, paid_date: date | None, category: str, filename: str) -> Path
```

Раскладка:

```
{archive_dir}/archiwum/wg-daty-dokumentu/{YYYY}/{MM-miesiac}/{kategoria}/{plik}
{archive_dir}/archiwum/wg-daty-platnosci/{YYYY}/{MM-miesiac}/{kategoria}/{plik}
```

`paid_date is None` → `{archive_dir}/archiwum/wg-daty-platnosci/{NO_PAYMENT_DATE_DIR}/{plik}`,
без года, месяца и категории (F-5.2).

**И `category`, и `filename` проходят через `safe_filename`.** Оба приходят из
документа, то есть от постороннего: `FV/1/2026.pdf` не должен становиться
подкаталогами, `../..` — уводить выше архива. Дерево обязано быть структурно
безопасным само по себе, а не потому что вызывающий не забыл почистить.

---

## Готово — это когда

```bash
cd C:\Users\Huawei\source\segregator-route
python -m pytest tests/test_route_tree.py -q
```

даёт **24 passed**, и

```bash
python -m pytest tests/ -q
```

не роняет ничего из существующего.

Тесты лежат в `tests/test_route_tree.py`, сейчас красные:
`ModuleNotFoundError: No module named 'segregator.route.tree'`.

**Тесты не менять.** Считаешь тест неверным — `question` на доску.

---

## Зачем это нужно

Два дерева объявлены и не работают.

`paths.ensure_tree` (`paths.py:20-21`) создаёт `archiwum/wg-daty-dokumentu/` и
`archiwum/wg-daty-platnosci/`. А `_route_to_archive` (`service.py:384`) собирает
путь так:

```python
target_dir = self.archive_dir / year / month_folder / category_name
```

Мимо обоих. Скелет строится пустым, документы ложатся **рядом** с ним, второе
дерево не заполняется никогда. F-5.1 и F-5.2 в чеклисте стоят `○`, и это точно.

Второе дерево нужно не для симметрии: по дате документа считается налог, по
дате платежа — что фактически оплачено. Бухгалтер смотрит оба.

Собирать этот путь должно одно место, а не каждый вызывающий по памяти.
После твоего модуля `MonthNames` из `service.py` уезжает — это уже моя часть.

---

## Протокол доски

```bash
python coord/board.py claims
python coord/board.py post --from antigravity --kind claim \
  --paths src/segregator/route/tree.py \
  --subject "Взял TASK-004" --refs F-5.1 F-5.2
```

В конце:
```bash
python coord/board.py post --from antigravity --kind release \
  --paths src/segregator/route/tree.py \
  --subject "TASK-004 готов, 24 passed" --body "<дословный вывод pytest>"
```

---

## Коммит

```bash
git add src/segregator/route/tree.py
git commit -m "feat(route): два дерева архива и польские имена месяцев"
```

Только этот путь. `git add -A` — нет.
