# Задание 003 для Antigravity — `route/linking.py`

Читается вместе с `coord/ANTIGRAVITY.md` (постоянная инструкция). Здесь — только
то, что относится к этой задаче.

От TASK-002 **не зависит**: можно делать до, после или параллельно.

---

## Где работать

```
Каталог: C:\Users\Huawei\source\segregator-route
Ветка:   feat/route-tree           (уже создана, уже выкачена — checkout не нужен)
База:    fix/merge-readiness
```

То же дерево, что и TASK-002. Claude Code в `route/` не заходит.

---

## Что сделать

Создать **один файл**: `src/segregator/route/linking.py`.

Ничего больше не трогать. Проводку в конвейер делает Claude Code.

### Контракт

```python
def link_or_copy(src: Path, dst: Path) -> str
```

Возвращает `"hardlink"` или `"copy"` — что фактически произошло.

- **Зовёт именно `os.link`.** Тесты границы тома подменяют `os.link`;
  реализация через `Path.hardlink_to` контракту не соответствует и тесты
  не пройдёт.
- Родительские каталоги `dst` создаются.
- `dst` уже существует → `FileExistsError`, файл не трогается. Молчаливая
  перезапись здесь означает потерю чужого документа.
- `src` не существует → `FileNotFoundError`.
- `OSError` с `errno.EXDEV` (граница тома) → откат на `shutil.copy2`,
  возврат `"copy"`, плюс запись через `logging` на уровне `WARNING`, в тексте
  которой есть `EXDEV` или слово «тома». Это и есть «объясняется» из F-5.4.
- **Любая другая `OSError` пробрасывается.** Отказ в доступе, замаскированный
  под копию, тихо выключает дедупликацию на всём архиве.

```python
def same_physical_file(a: Path, b: Path) -> bool
```

«Это одна и та же физическая ссылка», а не «содержимое совпало». Две копии с
одинаковыми байтами → `False`. Сравнение по `os.stat`: `st_dev` **и** `st_ino`.
Несуществующий путь → `False`, не исключение: это запрос, а не действие.

```python
def relink(old: Path, new: Path) -> None
```

Переклассификация переносит ссылку, не плодит вторую (F-5.6).

- Родительские каталоги `new` создаются.
- `new` уже существует → `FileExistsError`, и `old` **остаётся на месте**:
  неудачный перенос не должен терять единственную ссылку на документ.
- После успеха `old` не существует, `new` существует и остаётся тем же
  физическим файлом, что и исходный blob.

---

## Готово — это когда

```bash
cd C:\Users\Huawei\source\segregator-route
python -m pytest tests/test_route_linking.py -q
```

даёт **14 passed**, и

```bash
python -m pytest tests/ -q
```

не роняет ничего из существующего.

Тесты лежат в `tests/test_route_linking.py`, сейчас красные:
`ModuleNotFoundError: No module named 'segregator.route.linking'`.

**Тесты не менять.** Считаешь тест неверным — `question` на доску.

---

## Зачем это нужно

`_route_to_archive` (`service.py:395`) делает `shutil.copy2`. Решение записано
дважды — `SPEC.md:187-189` и `FUNCTIONAL_CHECKLIST.md:236` (F-5.3, «hardlink,
не копия») — и в коде не исполнено ни разу.

Цена конкретная. Одни и те же байты получают до **трёх** мест на диске: blob,
дерево по дате документа, дерево по дате платежа. На машине свободно ~19 ГБ
из 238. Архив бухгалтерии по закону хранится 5 лет.

Hardlink на NTFS работает без режима разработчика — в отличие от симлинка,
поэтому выбран именно он. Единственный случай, когда он невозможен, — разные
тома; его и надо поймать отдельно от всех прочих ошибок, а не глушить целиком
`except OSError`.

---

## Протокол доски

```bash
python coord/board.py claims
python coord/board.py post --from antigravity --kind claim \
  --paths src/segregator/route/linking.py \
  --subject "Взял TASK-003" --refs blocker-8 F-5.3 F-5.4 F-5.6
```

В конце:
```bash
python coord/board.py post --from antigravity --kind release \
  --paths src/segregator/route/linking.py \
  --subject "TASK-003 готов, 14 passed" --body "<дословный вывод pytest>"
```

---

## Коммит

```bash
git add src/segregator/route/linking.py
git commit -m "feat(route): hardlink вместо копии, откат через границу тома, перенос ссылки"
```

Только этот путь. `git add -A` — нет.
