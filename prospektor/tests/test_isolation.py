"""Страж границы между prospektor и segregator.

Изоляция, которая держится на договорённости, не держится. Здесь она
проверяется машинно, и тест обязан краснеть при первой же попытке
переступить границу — в любую сторону.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
SRC = MODULE_ROOT / "src" / "prospektor"
REPO_ROOT = MODULE_ROOT.parent

# Зависимости, объявленные в pyproject. Всё, что не отсюда, не из stdlib и не
# из самого пакета, — нарушение границы.
DECLARED = {
    "pydantic", "pydantic_settings", "httpx", "typer", "structlog", "rich", "yaml",
    "duckdb", "openpyxl", "playwright", "fastapi", "uvicorn", "jinja2", "pytest", "respx",
}


FORBIDDEN_PATH = re.compile(
    r"""["'][^"']*?(?:archiwum|blobs|rejestry|logs|JDG|ChatExport_)[/\\]""",
    re.IGNORECASE,
)


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def stray_imports(source: str, filename: str = "<test>") -> list[str]:
    """Импорты, уводящие за границу модуля. Статический разбор, без выполнения кода."""
    tree = ast.parse(source, filename=filename)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(_top_level(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # относительный импорт внутри пакета
                continue
            if node.module:
                imported.add(_top_level(node.module))
    allowed = DECLARED | set(sys.stdlib_module_names) | {"prospektor"}
    return sorted(imported - allowed)


def test_module_has_sources() -> None:
    assert _python_files(), "не найдено ни одного файла модуля — тест бесполезен"


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_imports_across_the_boundary(path: Path) -> None:
    """Ни один импорт модуля не должен уводить за его пределы."""
    stray = stray_imports(path.read_text(encoding="utf-8"), str(path))
    assert not stray, (
        f"{path.relative_to(MODULE_ROOT)} импортирует за границей модуля: {stray}. "
        "Либо это код segregator (запрещено), либо новая зависимость — "
        "тогда объяви её в pyproject.toml и в DECLARED."
    )


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_segregator_names(path: Path) -> None:
    """Прямых упоминаний пакета segregator быть не должно даже в строках."""
    text = path.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert not re.search(r"\bfrom\s+segregator\b|\bimport\s+segregator\b", code), (
        f"{path.relative_to(MODULE_ROOT)} тянет код основного проекта"
    )


def test_segregator_does_not_import_prospektor() -> None:
    """Обратное направление: основной проект не должен знать о модуле."""
    other_src = REPO_ROOT / "src"
    if not other_src.exists():
        pytest.skip("основной проект недоступен — проверка неприменима")
    offenders = [
        path
        for path in other_src.rglob("*.py")
        if re.search(r"\b(from|import)\s+prospektor\b", path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"segregator импортирует prospektor: {offenders}"


def test_paths_stay_inside_module() -> None:
    """Настройки не должны позволять писать за пределы каталога модуля."""
    from prospektor.config import MODULE_ROOT as CONFIG_ROOT
    from prospektor.config import Settings

    settings = Settings()
    assert settings.data_dir.is_relative_to(CONFIG_ROOT)
    assert settings.export_dir.is_relative_to(CONFIG_ROOT)

    with pytest.raises(ValueError, match="выходит за пределы модуля"):
        Settings(data_dir=REPO_ROOT / "archiwum")


def test_forbidden_paths_are_not_referenced() -> None:
    """В коде не должно быть путей к приватным каталогам основного проекта.

    Ищем именно пути, а не слова: «JDG» как термин предметной области в тексте
    комментария законен, «JDG/» как кусок пути — нет.
    """
    hits: list[str] = []
    for path in _python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN_PATH.search(line):
                hits.append(f"{path.relative_to(MODULE_ROOT)}:{number}")
    assert not hits, f"код ссылается на приватные каталоги segregator: {hits}"


# --- проверка самого стража -------------------------------------------------
#
# Тест изоляции — это и есть контракт границы, поэтому мало, чтобы он был
# зелёным: надо показать, что он краснеет на реальном нарушении. Иначе зелёная
# галочка ничего не доказывает.


def test_страж_ловит_импорт_основного_проекта() -> None:
    assert stray_imports("from segregator.config import Settings\n") == ["segregator"]
    assert stray_imports("import segregator.cli\n") == ["segregator"]


def test_страж_ловит_незаявленную_зависимость() -> None:
    assert stray_imports("import bs4\n") == ["bs4"]


def test_страж_пропускает_stdlib_и_объявленное() -> None:
    чисто = "import json\nimport httpx\nfrom prospektor.models import Business\n"
    assert stray_imports(чисто) == []


def test_страж_ловит_путь_к_приватным_данным() -> None:
    assert FORBIDDEN_PATH.search('BAD = "../archiwum/2026/faktura.pdf"')
    assert FORBIDDEN_PATH.search("path = 'C:/Users/x/JDG/skan.pdf'")
    assert FORBIDDEN_PATH.search('p = "blobs/deadbeef"')


def test_страж_не_придирается_к_термину_JDG_в_тексте() -> None:
    """JDG — форма ведения бизнеса; слово законно встречается в комментариях."""
    assert not FORBIDDEN_PATH.search('"""Для JDG контакты — персональные данные."""')
