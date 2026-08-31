"""Segregator - probe 02: структура HTML-экспорта Telegram Desktop.

ВАЖНО: скрипт НЕ печатает содержимое документов, имён файлов, текстов
сообщений и настоящих дат. В отчёт идут только:
  - какие теги и CSS-классы встречаются и сколько раз;
  - скелеты ПРЕДСТАВИТЕЛЕЙ каждого вида сообщения (с файлом, с фото,
    с текстом), где текст заменён на длину, путь — на «каталог +
    расширение», а дата — на маску формата (цифры → N).

Этого достаточно, чтобы написать парсер, и недостаточно, чтобы узнать
что-либо о самих документах.

Запуск:
    python probe/02_html_structure.py
    python probe/02_html_structure.py "D:\\другой\\экспорт"

Отчёт: probe/probe_result_html.txt — прочитай глазами перед тем, как отдавать.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_EXPORT = Path(r"C:\Users\Huawei\source\segregator\JDG")
OUT = Path(__file__).parent / "probe_result_html.txt"

PAGE_RE = re.compile(r"^messages\d*\.html$", re.IGNORECASE)
MAX_BUFFERED = 40          # сколько сообщений держать в буфере для выбора
MAX_DEPTH = 7

# У этих тегов нет закрывающего — handle_endtag для них не вызовется, и
# счётчик глубины уехал бы, склеивая следующее сообщение с текущим.
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img",
    "input", "link", "meta", "param", "source", "track", "wbr",
})


def mask_date(value: str) -> str:
    """14.01.2025 10:12:00 UTC+01:00 -> NN.NN.NNNN NN:NN:NN UTC+NN:NN."""
    return re.sub(r"\d", "N", value)


def mask_path(value: str) -> str:
    """files/faktura-a.pdf -> <dir=files ext=.pdf>. Имя файла не раскрывается."""
    if not value:
        return "<empty>"
    p = value.replace("\\", "/")
    parts = p.rsplit("/", 1)
    directory = parts[0] if len(parts) == 2 else "."
    ext = Path(parts[-1]).suffix or "<none>"
    return f"<dir={directory} ext={ext}>"


def mask_attr(name: str, value: str) -> str:
    if name in ("href", "src"):
        return mask_path(value)
    if name == "title":
        return f"<fmt={mask_date(value)}>"
    if name == "id":
        return "<" + re.sub(r"\d+", "N", value) + ">"
    if name == "class":
        return value  # классы структурны, их и ищем
    return f"<len={len(value)}>"


class Message:
    """Скелет одного сообщения плюс признаки, по которым его выбирают."""

    def __init__(self, classes: str) -> None:
        self.classes = classes
        self.lines: list[str] = []
        self.has_file = False
        self.has_photo = False
        self.has_text = False

    @property
    def kind(self) -> str:
        if self.has_file:
            return "с файлом"
        if self.has_photo:
            return "с фото"
        if self.has_text:
            return "только текст"
        return "без вложения и текста"


class StructureProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tag_class = Counter()
        self.attrs_seen: dict[str, Counter] = {}
        self.date_formats = Counter()
        self.link_dirs = Counter()
        self.messages: list[Message] = []
        self.current: Message | None = None
        self.depth = 0
        self._text_len = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = d.get("class", "")
        self.tag_class[f"{tag}.{cls}" if cls else tag] += 1
        self.attrs_seen.setdefault(tag, Counter()).update(d.keys())

        if "title" in d and re.search(r"\d{2}[.:]\d{2}", d["title"]):
            self.date_formats[mask_date(d["title"])] += 1
        for a in ("href", "src"):
            if a in d:
                self.link_dirs[mask_path(d[a])] += 1

        # Служебные сообщения структурно тривиальны и одинаковы везде —
        # ради них парсер не пишут. Берём только "message default".
        starts_message = (
            tag == "div" and "message" in cls and "service" not in cls
        )
        if starts_message and len(self.messages) < MAX_BUFFERED:
            self.current = Message(cls)
            self.messages.append(self.current)
            self.depth = 0

        if self.current is not None:
            if "media_file" in cls:
                self.current.has_file = True
            if "photo_wrap" in cls or (tag == "img" and "photo" in cls):
                self.current.has_photo = True

            if self.depth <= MAX_DEPTH:
                shown = " ".join(
                    f'{k}="{mask_attr(k, v)}"'
                    for k, v in d.items()
                    if k in ("class", "href", "src", "title", "id")
                )
                self.current.lines.append(f"{'  ' * self.depth}<{tag} {shown}>".rstrip())
            if tag not in VOID_TAGS:
                self.depth += 1

    def handle_endtag(self, tag):
        if self.current is None:
            return
        self.depth -= 1
        if self._text_len:
            self.current.lines.append(f"{'  ' * (self.depth + 1)}<text len={self._text_len}>")
            self.current.has_text = True
            self._text_len = 0
        if self.depth <= 0:
            self.current = None

    def handle_data(self, data):
        if self.current is not None and data.strip():
            self._text_len += len(data.strip())


def pick_representatives(messages: list[Message]) -> list[Message]:
    """По одному представителю каждого вида — вот ради чего probe и нужен."""
    chosen: list[Message] = []
    seen_kinds: set[str] = set()
    for m in messages:
        key = f"{m.kind}|{'joined' if 'joined' in m.classes else 'plain'}"
        if key not in seen_kinds:
            seen_kinds.add(key)
            chosen.append(m)
    return chosen


def main() -> int:
    export = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXPORT
    if not export.is_dir():
        print(f"НЕТ ПАПКИ: {export}")
        return 1

    all_html = sorted(export.rglob("*.html"))
    pages = [p for p in all_html if p.parent == export and PAGE_RE.match(p.name)]
    nested = [p for p in all_html if p.parent != export and PAGE_RE.match(p.name)]
    attachments = [p for p in all_html if not PAGE_RE.match(p.name)]

    lines: list[str] = []
    w = lines.append

    w("SEGREGATOR PROBE 02 — структура HTML-экспорта")
    w(f"export: {export}")
    w("")
    w("== КЛАССИФИКАЦИЯ HTML-ФАЙЛОВ ==")
    w(f"страницы экспорта в корне ({len(pages)}) — ТОЛЬКО ОНИ разбираются:")
    for p in pages:
        w(f"    {p.name:44} {p.stat().st_size/1024:>8.1f} KB")
    w(f"страницы во вложенных каталогах ({len(nested)}) — ОТДЕЛЬНЫЙ экспорт, не трогаем:")
    for p in nested:
        w(f"    {p.relative_to(export).as_posix():44} {p.stat().st_size/1024:>8.1f} KB")
    w(f".html как вложения ({len(attachments)}) — это документы, не переписка:")
    for p in attachments:
        w(f"    {p.relative_to(export).as_posix():44} {p.stat().st_size/1024:>8.1f} KB")
    w("")

    if not pages:
        w("!! В корне нет messages*.html — разбирать нечего.")
        report = "\n".join(lines)
        OUT.write_text(report, encoding="utf-8")
        print(report)
        return 1

    probe = StructureProbe()
    for f in pages:
        probe.feed(f.read_text(encoding="utf-8", errors="replace"))

    w("== ТЕГИ И КЛАССЫ (только страницы экспорта, топ-40) ==")
    for key, n in probe.tag_class.most_common(40):
        w(f"    {n:>6}  {key}")
    w("")

    w("== АТРИБУТЫ ПО ТЕГАМ ==")
    for tag in sorted(probe.attrs_seen):
        w(f"    {tag:<12} {', '.join(sorted(probe.attrs_seen[tag]))}")
    w("")

    w("== ФОРМАТЫ ДАТ (цифры заменены на N) ==")
    for fmt, n in probe.date_formats.most_common(10):
        w(f"    {n:>6}  {fmt}")
    w("")

    w("== КУДА ВЕДУТ ССЫЛКИ (каталог + расширение, без имён) ==")
    for d, n in probe.link_dirs.most_common(25):
        w(f"    {n:>6}  {d}")
    w("")

    reps = pick_representatives(probe.messages)
    w(f"== СКЕЛЕТЫ ПРЕДСТАВИТЕЛЕЙ ({len(reps)} из {len(probe.messages)} разобранных) ==")
    w("(значения вырезаны: текст → длина, путь → каталог+расширение, дата → маска)")
    w("")
    for m in reps:
        w(f"--- вид: {m.kind}; классы: {m.classes} ---")
        lines.extend(m.lines)
        w("")

    w("== END ==")

    report = "\n".join(lines)
    OUT.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nОтчёт: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
