"""Досье на лид: что сломано, чем это подтверждается, что предлагать.

Текст собирается кодом из сигналов и весов профиля — без участия модели.
Причина та же, что в основном проекте: цифра, которую нельзя проследить до
источника, не имеет права попасть в документ, который понесут к клиенту.
Модель, если её подключат, может переписать формулировки — но не числа.
"""

from __future__ import annotations

import json
from typing import Any

from prospektor.models import Business
from prospektor.scoring import Profile, Scored

# Что предлагать по каждому промаху и почему это стоит денег.
# Это словарь предложений, а не «рекомендации ИИ»: он написан один раз,
# проверяется глазами и меняется осознанно.
REMEDIES: dict[str, tuple[str, str]] = {
    "ops.own_order_channel": (
        "Поднять собственный приём заказов (виджет на сайте + оплата)",
        "Заказ через свой канал не отдаёт комиссию агрегатору "
        "и оставляет контакт клиента у заведения",
    ),
    "ops.aggregator_only": (
        "Вывести часть оборота из агрегатора в свой канал",
        "Комиссия агрегаторов около 30%; даже перевод четверти заказов заметен в марже",
    ),
    "ops.phone_only": (
        "Добавить онлайн-приём заказов и запись",
        "Всё, что приходит вне рабочих часов, сейчас теряется целиком",
    ),
    "aio.localbusiness_schema": (
        "Внедрить разметку Schema.org (LocalBusiness/Restaurant)",
        "Без неё ассистенту нечего процитировать — в ответ попадает конкурент с разметкой",
    ),
    "aio.menu_machine_readable": (
        "Опубликовать меню разметкой Menu/MenuItem с ценами",
        "Запросы вида «острое, без глютена, до 50 zł» фильтруют заведение только по разметке",
    ),
    "aio.menu_is_image_only": (
        "Перевести меню из картинки/PDF в текст с разметкой",
        "Состав и цены сейчас не читаются ни поиском, ни ассистентом",
    ),
    "aio.ai_crawlers_allowed": (
        "Открыть в robots.txt роботов ИИ-поиска",
        "Закрытый GPTBot/ClaudeBot/PerplexityBot означает отсутствие в ответах ассистентов",
    ),
    "aio.diet_tags": (
        "Проставить теги диет и аллергенов (suitableForDiet)",
        "Фильтр «без глютена» отсекает заведения без тегов ещё до показа пользователю",
    ),
    "aio.js_dependent": (
        "Отдавать основной контент в HTML, не только после JS",
        "Робот без выполнения скриптов видит пустую страницу",
    ),
    "aio.jsonld_broken": (
        "Починить существующую JSON-LD разметку",
        "Блок есть, но не разбирается — самая дешёвая правка из всего списка",
    ),
    "web.has_site": (
        "Сделать посадочную страницу с меню, часами и кнопкой заказа",
        "Сейчас показать агенту и человеку нечего",
    ),
    "web.only_social": (
        "Завести собственный сайт вместо страницы в соцсети",
        "Соцсеть не отдаёт разметку и владеет каналом; правила меняются без спроса",
    ),
    "web.is_placeholder": ("Заменить заглушку на рабочий сайт", "Домен занят, но не работает"),
    "web.mobile_viewport": (
        "Починить мобильную вёрстку",
        "Большая часть заказов приходит с телефона",
    ),
    "seo.indexable": ("Снять запрет индексации", "Сайт закрыт от поиска целиком"),
    "seo.title_ok": ("Переписать title", "Заголовок не помещается в выдачу или пуст"),
    "seo.description_ok": ("Переписать description", "Сниппет формируется случайно"),
    "seo.nap_phone_match": (
        "Свести телефон на сайте, карте и в реестре",
        "Расхождение NAP снижает локальные позиции и путает ассистента",
    ),
    "ops.reservation": ("Подключить онлайн-запись", "Бронь по телефону теряется вне часов работы"),
    "ops.chat": ("Добавить чат или мессенджер", "Часть вопросов не доходит до звонка"),
}


def render_dossier(
    biz: Business,
    scored: Scored,
    signals: dict[str, Any],
    evidence: dict[str, str],
    profile: Profile,
) -> str:
    lines: list[str] = []
    lines.append(f"# {biz.name}")
    lines.append("")
    where = ", ".join(p for p in (biz.street, biz.city) if p) or "адрес неизвестен"
    lines.append(f"{where} · профиль **{profile.label}**")
    lines.append("")
    lines.append(
        f"**Разрыв {scored.gap:.0f}/100** · **Потенциал {scored.fit:.0f}/100** · "
        f"**Приоритет {scored.priority:.0f}**"
    )
    lines.append("")

    contacts = []
    if biz.website:
        contacts.append(f"сайт: {biz.website}")
    if biz.phone:
        contacts.append(f"тел.: {biz.phone}")
    if biz.email:
        contacts.append(f"почта: {biz.email}")
    if contacts:
        lines.append("Контакты — " + "; ".join(contacts))
        lines.append("")

    demand = []
    if biz.rating is not None:
        demand.append(f"рейтинг {biz.rating}")
    if biz.reviews_count is not None:
        demand.append(f"{biz.reviews_count} отзывов")
    if biz.cuisines:
        demand.append("кухни: " + ", ".join(biz.cuisines))
    if demand:
        lines.append("Спрос — " + ", ".join(demand))
        lines.append("")

    misses = scored.breakdown.get("misses", [])
    if not misses:
        lines.append("Проверенных дефектов нет — как лид не интересно.")
        return "\n".join(lines)

    lines.append("## Что сломано")
    lines.append("")
    for miss in misses:
        signal = miss["signal"]
        title, why = REMEDIES.get(signal, (signal, miss.get("why") or ""))
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"- Сигнал: `{signal}` = `{miss['actual']}` (ожидалось `{miss['expected']}`)")
        lines.append(f"- Вес в оценке: {miss['weight']:.0f}")
        if why:
            lines.append(f"- Почему это стоит денег: {why}")
        proof = evidence.get(signal)
        if proof:
            lines.append(f"- Проверено на: {proof}")
        lines.append("")

    extra = _context_notes(signals)
    if extra:
        lines.append("## Контекст")
        lines.append("")
        lines.extend(f"- {note}" for note in extra)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Список подготовлен автоматически. Проверка фактов и любой контакт с заведением — "
        "за человеком: модуль готовит файл, связывается человек._"
    )
    return "\n".join(lines)


def _context_notes(signals: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    aggregators = signals.get("ops.aggregators")
    if aggregators:
        notes.append("Присутствует в агрегаторах: " + ", ".join(aggregators))
    widgets = signals.get("ops.order_widgets")
    if widgets:
        notes.append("Свой движок заказа: " + ", ".join(widgets))
    cms = signals.get("web.cms")
    if cms and cms != "unknown":
        notes.append(f"Сайт на {cms} — разметку можно поставить без переписывания")
    blocked = signals.get("aio.ai_crawlers_blocked")
    if blocked:
        notes.append("Закрыты ИИ-роботы: " + ", ".join(blocked))
    types = signals.get("aio.jsonld_types")
    if types:
        notes.append("Найденные типы разметки: " + ", ".join(types))
    year = signals.get("web.copyright_year")
    if year:
        notes.append(f"Год в подвале сайта: {int(year)}")
    return notes


def dossier_json(biz: Business, scored: Scored, signals: dict[str, Any]) -> str:
    """Машиночитаемая версия — если досье понадобится передать дальше."""
    return json.dumps(
        {
            "business": biz.model_dump(mode="json"),
            "gap": scored.gap,
            "fit": scored.fit,
            "priority": scored.priority,
            "misses": scored.breakdown.get("misses", []),
            "signals": signals,
        },
        ensure_ascii=False,
        indent=2,
    )
