"""Имена файлов, безопасные для NTFS, и разрешение коллизий (TASK-002).

Правила safe_filename:
1. Каждый запрещённый на NTFS символ <>:"/\\|?* и каждый управляющий символ
   (ord(c) < 32) заменяется на _. Один символ — один _.
2. С конца строки снимаются точки и пробелы (.rstrip(". ")).
3. Если часть имени до первой точки совпадает (без учёта регистра) с именем
   устройства (CON, PRN, AUX, NUL, COM1..COM9, LPT1..LPT9) — впереди добавляется _.
4. Основа (Path(name).stem) режется до max_stem символов, расширение (Path(name).suffix)
   сохраняется целиком.
5. Если после всего строка пуста — возвращается "bez-nazwy".
Польская диакритика сохраняется.
"""

from __future__ import annotations

from pathlib import Path

FORBIDDEN_CHARS = set('<>:"/\\|?*')
DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, *, max_stem: int = 120) -> str:
    """Очищает имя файла под правила NTFS."""
    # 1. Замена запрещённых и управляющих символов
    chars = [
        "_" if (c in FORBIDDEN_CHARS or ord(c) < 32) else c
        for c in name
    ]
    s = "".join(chars)

    # 2. Снятие точек и пробелов с конца
    s = s.rstrip(". ")

    # Если пусто после rstrip
    if not s:
        return "bez-nazwy"

    # 3. Имена устройств (до первой точки без учёта регистра)
    first_part = s.split(".")[0]
    if first_part.upper() in DEVICE_NAMES:
        s = "_" + s

    # 4. Обрезка stem до max_stem, suffix сохраняется
    p = Path(s)
    stem = p.stem[:max_stem]
    suffix = p.suffix
    result = stem + suffix

    # 5. Если после всего строка пуста
    if not result:
        return "bez-nazwy"

    return result


def unique_path(target: Path, *, limit: int = 999) -> Path:
    """Возвращает свободный путь, добавляя __2, __3... перед расширением при коллизии.

    Исчерпав limit попыток, поднимает FileExistsError.
    """
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    parent = target.parent

    for i in range(2, limit + 1):
        candidate = parent / f"{stem}__{i}{suffix}"
        if not candidate.exists():
            return candidate

    raise FileExistsError("Не удалось подобрать свободное имя файла: исчерпан лимит попыток")
