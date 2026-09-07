"""Компилятор фильтров: YAML-предикаты -> SQL.

Требование пользователя было «фильтровать по любым параметрам, в том числе
разовым тематическим». Отсюда решение: фильтр — это данные, а не код. Новый
критерий добавляется файлом в ``queries/``, а не правкой Python.

Поддерживаются три вида полей:

* колонки карточки — ``city``, ``country``, ``name``, ``rating``…;
* списки — ``category_in``, ``cuisine_in``;
* сигналы — всё, где есть точка: ``aio.menu_is_image_only``, ``ops.online_order``;
* оценки — ``gap``, ``fit``, ``priority``, ``reachable``.

Значение либо сравнивается на равенство, либо задаётся словарём операторов
``{gte: 80}``. Блоки ``any_of`` и ``all_of`` вкладываются друг в друга.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from prospektor.config import MODULE_ROOT

QUERY_DIR = MODULE_ROOT / "queries"

_OPS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_BUSINESS_COLUMNS = {
    "id", "name", "country", "city", "postal_code", "street",
    "phone", "email", "website", "rating", "reviews_count",
}
_SCORE_COLUMNS = {"gap", "fit", "priority", "reachable"}


class QueryError(ValueError):
    """Ошибка в описании фильтра. Сообщение адресовано человеку, а не логам."""


def load_query(name: str) -> dict[str, Any]:
    path = Path(name)
    if not path.exists():
        path = QUERY_DIR / (name if name.endswith(".yaml") else f"{name}.yaml")
    if not path.exists():
        raise QueryError(f"фильтр не найден: {name}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _signal_predicate(key: str, spec: Any, params: list[Any]) -> str:
    """Условие по сигналу — через EXISTS, чтобы не зависеть от плоского вью.

    Сигналы хранятся ключ-значением, поэтому новый сигнал доступен фильтру
    сразу, без миграции схемы.
    """
    base = "SELECT 1 FROM signals s WHERE s.business_id = b.id AND s.key = ?"
    if isinstance(spec, bool):
        params.extend([key, int(spec)])
        return f"EXISTS ({base} AND s.value_bool = ?)"
    if isinstance(spec, (int, float)):
        params.extend([key, spec])
        return f"EXISTS ({base} AND s.value_num = ?)"
    if isinstance(spec, str):
        params.extend([key, f"%{spec}%"])
        return f"EXISTS ({base} AND (s.value_json LIKE ? ))"
    if isinstance(spec, dict):
        clauses: list[str] = []
        for op, value in spec.items():
            if op == "contains":
                # Списковые сигналы (ops.aggregators) хранятся JSON-массивом
                params.extend([key, f'%"{value}"%'])
                clauses.append(f"EXISTS ({base} AND s.value_json LIKE ?)")
            elif op == "is_null":
                params.append(key)
                clause = f"NOT EXISTS ({base})" if value else f"EXISTS ({base})"
                clauses.append(clause)
            elif op in _OPS:
                params.extend([key, value])
                clauses.append(f"EXISTS ({base} AND s.value_num {_OPS[op]} ?)")
            else:
                raise QueryError(f"неизвестный оператор «{op}» для сигнала {key}")
        return "(" + " AND ".join(clauses) + ")"
    raise QueryError(f"не понимаю условие для сигнала {key}: {spec!r}")

def _column_predicate(column: str, spec: Any, params: list[Any]) -> str:
    target = f"sc.{column}" if column in _SCORE_COLUMNS else f"b.{column}"
    if isinstance(spec, list):
        placeholders = ", ".join("?" for _ in spec)
        params.extend(spec)
        return f"{target} IN ({placeholders})"
    if isinstance(spec, dict):
        clauses = []
        for op, value in spec.items():
            if op == "in":
                placeholders = ", ".join("?" for _ in value)
                params.extend(value)
                clauses.append(f"{target} IN ({placeholders})")
            elif op == "like":
                params.append(f"%{value}%")
                clauses.append(f"{target} LIKE ?")
            elif op == "is_null":
                clauses.append(f"{target} IS {'NULL' if value else 'NOT NULL'}")
            elif op in _OPS:
                params.append(value)
                clauses.append(f"{target} {_OPS[op]} ?")
            else:
                raise QueryError(f"неизвестный оператор «{op}» для поля {column}")
        return "(" + " AND ".join(clauses) + ")"
    if spec is None:
        return f"{target} IS NULL"
    params.append(spec)
    return f"{target} = ?"


def _list_predicate(column: str, values: Any, params: list[Any]) -> str:
    """Пересечение со списком в JSON-колонке карточки (категории, кухни)."""
    if not isinstance(values, list):
        values = [values]
    clauses = []
    for value in values:
        params.append(f'%"{value}"%')
        clauses.append(f"b.{column} LIKE ?")
    return "(" + " OR ".join(clauses) + ")"


def _condition(node: Any, params: list[Any]) -> str:
    if not isinstance(node, dict):
        raise QueryError(f"условие должно быть отображением, получено: {node!r}")
    clauses: list[str] = []
    for key, spec in node.items():
        if key == "any_of":
            clauses.append("(" + " OR ".join(_condition(c, params) for c in spec) + ")")
        elif key == "all_of":
            clauses.append("(" + " AND ".join(_condition(c, params) for c in spec) + ")")
        elif key == "not":
            clauses.append("NOT (" + _condition(spec, params) + ")")
        elif key == "category_in":
            clauses.append(_list_predicate("categories", spec, params))
        elif key == "cuisine_in":
            clauses.append(_list_predicate("cuisines", spec, params))
        elif "." in key:
            clauses.append(_signal_predicate(key, spec, params))
        elif key in _BUSINESS_COLUMNS or key in _SCORE_COLUMNS:
            clauses.append(_column_predicate(key, spec, params))
        else:
            raise QueryError(
                f"неизвестное поле «{key}». Поля карточки: {sorted(_BUSINESS_COLUMNS)}; "
                f"оценки: {sorted(_SCORE_COLUMNS)}; сигналы пишутся через точку"
            )
    return "(" + " AND ".join(clauses) + ")" if clauses else "1=1"


def compile_query(spec: dict[str, Any], profile: str | None = None) -> tuple[str, list[Any]]:
    params: list[Any] = []
    conditions = spec.get("where") or []
    if isinstance(conditions, dict):
        conditions = [conditions]
    where = " AND ".join(_condition(c, params) for c in conditions) or "1=1"

    join_params: list[Any] = [profile or spec.get("profile") or "horeca_pl"]
    order = spec.get("order_by") or "priority desc"
    column, _, direction = order.partition(" ")
    direction = "DESC" if direction.strip().lower() == "desc" else "ASC"
    if column in _SCORE_COLUMNS:
        order_sql = f"sc.{column} {direction}"
    elif column in _BUSINESS_COLUMNS:
        order_sql = f"b.{column} {direction}"
    else:
        raise QueryError(f"нельзя сортировать по «{column}»")

    limit = int(spec.get("limit", 500))
    sql = f"""
        SELECT b.*, sc.gap, sc.fit, sc.priority, sc.reachable, sc.breakdown
        FROM businesses b
        LEFT JOIN scores sc ON sc.business_id = b.id AND sc.profile = ?
        WHERE {where}
        ORDER BY {order_sql} NULLS LAST
        LIMIT {limit}
    """
    return sql, join_params + params


def run_query(store: Any, spec: dict[str, Any], profile: str | None = None) -> list[dict[str, Any]]:
    sql, params = compile_query(spec, profile)
    return [dict(row) for row in store.conn.execute(sql, params)]
