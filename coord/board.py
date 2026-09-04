#!/usr/bin/env python3
"""Доска объявлений двух агентов.

Append-only JSONL: Claude Code и Antigravity работают в разных worktree одного
репозитория и не видят рабочих деревьев друг друга. Общее у них — каталог
`.git`, и доска лежит там же.

Почему не в рабочем дереве, как было сначала: у worktree рабочие копии разные.
Пока файл лежал в `coord/`, Claude Code писал в свою копию, Antigravity — в
свою, и прочитать друг друга они могли только после слияния веток. То есть
после столкновения, которое доска обязана предотвращать. Из четырёх записей
первых суток общими оказались две.

Почему JSONL, а не база или сервис: доска обязана читаться человеком без
инструментов — она открывается блокнотом. Ровно по той же причине так устроен
`.ai-loop/ledger.jsonl`.

Хеш-цепочки здесь намеренно нет, и в git доска больше не попадает. Ledger
доказывает, что решение было принято, и подделка там меняет смысл записи.
Доска — переписка: подделывать её незачем, а цена лишней церемонии — что ею
перестанут пользоваться. Доказательством она не была и раньше; за историей
идти в ledger и в `git log`.

Честная граница: протокол кооперативный. Ни один агент не может быть принуждён
писать сюда. Работу реально разводят раздельные worktree, раздельные ветки и
гейт ai-loop; доска снижает частоту столкновений, а не делает их невозможными.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _board_path() -> Path:
    """Путь к доске в общем каталоге `.git` — одном на все рабочие деревья."""
    here = Path(__file__).resolve().parent
    try:
        common = subprocess.check_output(
            ["git", "-C", str(here), "rev-parse", "--git-common-dir"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit(f"board.py: {here} не в git-репозитории — доски нет")
    # Из связанного worktree git отдаёт абсолютный путь, из главного —
    # относительный к каталогу, переданному в -C. `here / common` верен для
    # обоих: абсолютный правый операнд отбрасывает левый.
    return (here / common).resolve() / "coord" / "board.jsonl"


BOARD = _board_path()

KINDS = ("claim", "release", "question", "answer", "handoff", "note")
ACTORS = ("claude", "antigravity", "vlad")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> list[dict]:
    if not BOARD.exists():
        return []
    records = []
    for line_no, line in enumerate(BOARD.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # Битую строку не глотаем и не чиним: доска append-only, значит
            # порчу внёс человек или сбой записи — и об этом надо знать.
            print(f"board.jsonl:{line_no}: строка не разбирается как JSON", file=sys.stderr)
    return records


def post(args: argparse.Namespace) -> int:
    record = {
        "ts": _now(),
        "from": args.sender,
        "to": args.to,
        "kind": args.kind,
        "subject": args.subject,
        "body": args.body or "",
        "paths": args.paths or [],
        "refs": args.refs or [],
    }
    BOARD.parent.mkdir(parents=True, exist_ok=True)
    with BOARD.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def tail(args: argparse.Namespace) -> int:
    records = _read()[-args.n :]
    if not records:
        print("доска пуста")
        return 0
    for r in records:
        paths = f"  [{', '.join(r.get('paths') or [])}]" if r.get("paths") else ""
        print(f"{r['ts']}  {r['from']:12} -> {r.get('to') or 'все':12} {r['kind']:9} {r.get('subject','')}{paths}")
        if args.full and r.get("body"):
            for line in r["body"].splitlines():
                print(f"      {line}")
    return 0


def claims(args: argparse.Namespace) -> int:
    """Открытые заявки: claim без парного release по тем же путям.

    Сравнение по префиксу пути, а не по равенству: заявка на каталог
    закрывает и файлы под ним.
    """
    open_claims: list[dict] = []
    for r in _read():
        if r["kind"] == "claim":
            open_claims.append(r)
        elif r["kind"] == "release":
            released = set(r.get("paths") or [])
            open_claims = [
                c
                for c in open_claims
                if not (
                    c["from"] == r["from"]
                    and any(
                        p.startswith(rel) or rel.startswith(p)
                        for p in (c.get("paths") or [])
                        for rel in released
                    )
                )
            ]

    if not open_claims:
        print("открытых заявок нет")
        return 0
    for c in open_claims:
        print(f"{c['ts']}  {c['from']:12} {c.get('subject','')}")
        for p in c.get("paths") or []:
            print(f"      {p}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_post = sub.add_parser("post", help="написать на доску")
    p_post.add_argument("--from", dest="sender", required=True, choices=ACTORS)
    p_post.add_argument("--to", default=None, choices=ACTORS)
    p_post.add_argument("--kind", required=True, choices=KINDS)
    p_post.add_argument("--subject", required=True)
    p_post.add_argument("--body", default="")
    p_post.add_argument("--paths", nargs="*", default=[])
    p_post.add_argument("--refs", nargs="*", default=[])
    p_post.set_defaults(func=post)

    p_tail = sub.add_parser("tail", help="последние записи")
    p_tail.add_argument("-n", type=int, default=20)
    p_tail.add_argument("--full", action="store_true", help="печатать тело сообщений")
    p_tail.set_defaults(func=tail)

    p_claims = sub.add_parser("claims", help="какие области сейчас заняты")
    p_claims.set_defaults(func=claims)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
