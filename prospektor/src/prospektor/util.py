"""Мелкие утилиты: идентификаторы, нормализация имён, геометрия."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata

# Юридические формы и мусор, которые не несут смысла при сопоставлении названий.
_NOISE = re.compile(
    r"\b(sp\.?\s*z\s*o\.?\s*o\.?|s\.?a\.?|sp\.?\s*j\.?|sp\.?\s*k\.?|spolka|spółka"
    r"|jednoosobowa|firma|zaklad|zakład|p\.?p\.?h\.?u\.?|f\.?h\.?u\.?"
    r"|restauracja|restaurant|pizzeria|kawiarnia|bar|cafe)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def slug_name(name: str) -> str:
    """Нормализовать название для сопоставления между источниками.

    Убирает диакритику, юридическую форму и родовое слово: «Restauracja Wok & Roll
    Sp. z o.o.» и «Wok and Roll» должны сойтись.
    """
    text = unicodedata.normalize("NFKD", name.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ").replace("ł", "l")
    text = _NOISE.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def business_id(name: str, lat: float | None, lon: float | None, country: str = "PL") -> str:
    """Стабильный идентификатор: имя + округлённая точка (~110 м).

    Округление — компромисс: карты расходятся в координатах одного и того же
    заведения на десятки метров, а соседние дома по одному адресу различать надо.
    """
    key = f"{country}|{slug_name(name)}|"
    if lat is not None and lon is not None:
        key += f"{lat:.3f},{lon:.3f}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между точками в метрах."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalize_phone(raw: str, default_cc: str = "48") -> str | None:
    """Привести телефон к E.164. Нужен для сшивания источников и для NAP-сверки."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if not digits:
        return None
    if digits.startswith("+"):
        return digits
    digits = digits.lstrip("0")
    if len(digits) == 9:  # польский национальный номер
        return f"+{default_cc}{digits}"
    if digits.startswith(default_cc):
        return f"+{digits}"
    return f"+{digits}" if len(digits) > 9 else None


def registrable_domain(url: str) -> str | None:
    """Хост без ``www``. Хватает для сопоставления сайта с карточкой на карте."""
    match = re.match(r"^\s*(?:https?://)?([^/?#:]+)", url or "", re.IGNORECASE)
    if not match:
        return None
    host = match.group(1).lower().removeprefix("www.")
    return host or None
