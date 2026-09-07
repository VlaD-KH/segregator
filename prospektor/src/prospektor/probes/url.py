"""Проба по адресу, без загрузки страницы.

Отдельная от остальных, потому что работает раньше них: ей достаточно поля
`website`, которое приходит из карты на стадии discover. Прогон по Старгарду
показал, что 8% таких адресов ведут не на сайт бизнеса, а на его профиль в
Booksy, страницу в Facebook или короткую ссылку Google.

Практический смысл: часть сигналов группы ops становится известна **до** аудита
и без единого сетевого запроса. Для вертикали красоты это сразу готовый список —
салоны, у которых всё веб-присутствие сводится к профилю на чужой платформе.
"""

from __future__ import annotations

from prospektor.models import Business, Evidence, Signal
from prospektor.platforms import UrlKind, classify_url

# Виды адресов, означающие «своего сайта нет, есть профиль на чужой площадке»
_PROFILE_KINDS = frozenset(
    {UrlKind.BOOKING, UrlKind.DELIVERY, UrlKind.SOCIAL, UrlKind.DIRECTORY,
     UrlKind.MARKETPLACE, UrlKind.MAPS_LINK}
)


def probe_business(biz: Business) -> list[Signal]:
    """Сигналы, выводимые из одного лишь адреса в карточке."""
    url = (biz.website or "").strip()
    kind, platform = classify_url(url)
    evidence = Evidence(url=url, note="классификация адреса без загрузки") if url else None

    out: list[Signal] = [Signal.data("web.url_kind", str(kind), evidence)]
    if platform:
        out.append(Signal.data("web.url_platform", platform, evidence))

    profile_only = kind in _PROFILE_KINDS
    out.append(Signal.flag("web.platform_profile_only", profile_only, evidence))

    # Владельцем сигналов ops.reservation и ops.online_order остаётся проба ops:
    # два источника одного сигнала перезаписывали бы друг друга. Здесь только факт
    # платформы, который проба ops учтёт вместе с найденным на странице.
    if kind is UrlKind.BOOKING:
        out.append(Signal.data("ops.booking_platform", platform, evidence))
    if kind is UrlKind.DELIVERY:
        out.append(Signal.data("ops.delivery_platform", platform, evidence))

    return out
