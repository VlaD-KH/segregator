"""Скоринг: детерминированный расчёт по YAML-профилю.

Ни одна цифра здесь не приходит от модели. Балл — сумма весов правил, каждое
из которых ссылается на конкретный сигнал, а каждый сигнал — на evidence.
Разбор ``breakdown`` сохраняется целиком, поэтому на вопрос «почему 72, а не
40» всегда есть построчный ответ.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from prospektor.config import MODULE_ROOT
from prospektor.models import Business, utcnow
from prospektor.store import Store

PROFILE_DIR = MODULE_ROOT / "profiles"


@dataclass
class Rule:
    signal: str
    expect: Any
    weight: float
    why: str | None = None


@dataclass
class Profile:
    name: str
    label: str
    categories: str | None
    rules: list[Rule]
    fit: dict[str, Any]
    alpha: float = 0.55

    @property
    def total_weight(self) -> float:
        return sum(r.weight for r in self.rules) or 1.0


@dataclass
class Scored:
    gap: float
    fit: float
    priority: float
    reachable: bool
    breakdown: dict[str, Any] = field(default_factory=dict)


def load_profile(name: str) -> Profile:
    path = Path(name)
    if not path.exists():
        path = PROFILE_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"профиль не найден: {name}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = [
        Rule(
            signal=r["signal"],
            expect=r.get("expect", True),
            weight=float(r.get("weight", 1)),
            why=r.get("why"),
        )
        for r in (data.get("gap") or {}).get("rules", [])
    ]
    return Profile(
        name=data.get("name", path.stem),
        label=data.get("label", path.stem),
        categories=data.get("categories"),
        rules=rules,
        fit=data.get("fit") or {},
        alpha=float((data.get("priority") or {}).get("alpha", 0.55)),
    )


def _gap(profile: Profile, signals: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    earned = 0.0
    considered = 0.0
    misses: list[dict[str, Any]] = []
    for rule in profile.rules:
        actual = signals.get(rule.signal)
        if actual is None:
            # Сигнал не измерен — правило не участвует ни в числителе, ни в
            # знаменателе. Иначе непроверенный сайт получал бы тот же балл,
            # что и проверенный и целый.
            continue
        considered += rule.weight
        if actual != rule.expect:
            earned += rule.weight
            misses.append(
                {
                    "signal": rule.signal,
                    "expected": rule.expect,
                    "actual": actual,
                    "weight": rule.weight,
                    "why": rule.why,
                }
            )
    if considered == 0:
        return 0.0, misses
    return round(100 * earned / considered, 1), sorted(
        misses, key=lambda m: -m["weight"]
    )


def _fit(profile: Profile, biz: Business, signals: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    spec = profile.fit
    parts: dict[str, float] = {}

    reviews_spec = spec.get("reviews") or {}
    reviews = float(biz.reviews_count or signals.get("rep.reviews_count") or 0)
    saturate = float(reviews_spec.get("saturate_at", 300))
    if reviews_spec.get("log_scale", True):
        # log1p: разница между 10 и 100 отзывами весит больше, чем между 400 и 500
        share = math.log1p(reviews) / math.log1p(saturate) if saturate > 0 else 0.0
    else:
        share = reviews / saturate if saturate else 0.0
    parts["reviews"] = min(1.0, share) * float(reviews_spec.get("weight", 0))

    rating_spec = spec.get("rating") or {}
    rating = biz.rating if biz.rating is not None else signals.get("rep.rating")
    if rating is None:
        # Рейтинг неизвестен — берём середину, а не ноль: отсутствие данных
        # не должно наказывать заведение.
        rating_share = 0.5
    else:
        floor = float(rating_spec.get("floor", 3.0))
        ceiling = float(rating_spec.get("ceiling", 5.0))
        rating_share = min(1.0, max(0.0, (float(rating) - floor) / max(ceiling - floor, 0.1)))
    parts["rating"] = rating_share * float(rating_spec.get("weight", 0))

    reachable = bool(signals.get("rep.contactable") or biz.phone or biz.email or biz.website)
    parts["reachability"] = float((spec.get("reachability") or {}).get("weight", 0)) * (
        1.0 if reachable else 0.0
    )

    total_weight = sum(
        float((spec.get(key) or {}).get("weight", 0))
        for key in ("reviews", "rating", "reachability")
    ) or 1.0
    value = round(100 * sum(parts.values()) / total_weight, 1)
    return value, {k: round(v, 2) for k, v in parts.items()}


def score_business(profile: Profile, biz: Business, signals: dict[str, Any]) -> Scored:
    gap, misses = _gap(profile, signals)
    fit, fit_parts = _fit(profile, biz, signals)
    alpha = profile.alpha
    # Геометрическое среднее, а не сумма: нулевой fit обнуляет приоритет,
    # каким бы большим ни был gap.
    priority = round((gap**alpha) * (fit ** (1 - alpha)), 1) if gap and fit else 0.0
    reachable = bool(signals.get("rep.contactable") or biz.phone or biz.email)
    return Scored(
        gap=gap,
        fit=fit,
        priority=priority,
        reachable=reachable,
        breakdown={"misses": misses, "fit": fit_parts},
    )


def score_all(store: Store, profile: Profile) -> int:
    count = 0
    for biz in store.iter_businesses():
        signals = store.signals_for(biz.id)
        if not signals:
            continue
        scored = score_business(profile, biz, signals)
        with store.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scores (business_id, profile, gap, fit, priority, "
                "reachable, breakdown, scored_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    biz.id,
                    profile.name,
                    scored.gap,
                    scored.fit,
                    scored.priority,
                    int(scored.reachable),
                    json.dumps(scored.breakdown, ensure_ascii=False),
                    utcnow().isoformat(),
                ),
            )
        count += 1
    return count
