#!/usr/bin/env python3
"""Operator surface for an ai-loop run: start, gate, approve, advance, status.

This is the one command the loop skills call. It ties the deterministic
classifier to the append-only ledger so that no phase can advance without the
decision being recorded, and no decision can be recorded without a
classification behind it.

    loop.py start "add rate limiting to the public API"
    loop.py enter    --run-id R --phase BUILD
    loop.py gate     --run-id R --phase BUILD --cumulative
    loop.py approve  --run-id R --actor vlad --note "reviewed diff, ok"
    loop.py reject   --run-id R --actor vlad --note "spec is wrong"
    loop.py complete --run-id R --phase BUILD
    loop.py status   [--run-id R]
    loop.py finish   --run-id R

Exit codes for `gate` match classify_diff.py: 0 allow, 10 require_human,
2 error (treat as require_human).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify_diff  # noqa: E402
import ledger  # noqa: E402

PHASES = ["INTAKE", "GROUND", "CONTROL", "DEFINE", "PLAN", "BUILD", "VERIFY", "REVIEW", "SHIP", "DONE"]


def _slug(text: str, limit: int = 32) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "run").lower()).strip("-")
    return (slug[:limit] or "run").strip("-")


def _run_id(idea: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{_slug(idea)}"


def diff_fingerprint(repo: str, staged: bool = False, rng: str | None = None) -> str | None:
    """SHA-256 того самого диффа, который классифицировали.

    Подпись человека обязана относиться к конкретному снимку. Без отпечатка
    запись «vlad approved» не отличает дифф, который он читал, от того, что
    оказался в дереве через минуту.

    `.ai-loop/ledger.jsonl` исключён из вычисления пути. Он сам версионируется,
    и каждый append дописывает в него строку — в том числе запись самой
    классификации, которую fingerprint должен запечатлеть. Без исключения
    любой gate делал бы собственный отпечаток протухшим в момент своего же
    завершения: seq N добавляет строку N в ledger.jsonl, `git diff HEAD` эту
    строку тут же видит, и следующий approve сравнивает уже другой дифф.
    Журнал — это протокол проверки, а не часть проверяемого изменения.
    """
    exclude_ledger = f":(exclude){ledger.LEDGER_REL}"
    if rng:
        argv = ["git", "-C", os.path.abspath(repo), "diff", rng, "--", ".", exclude_ledger]
    elif staged:
        argv = ["git", "-C", os.path.abspath(repo), "diff", "--cached", "--", ".", exclude_ledger]
    else:
        argv = ["git", "-C", os.path.abspath(repo), "diff", "HEAD", "--", ".", exclude_ledger]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return "sha256:" + hashlib.sha256(done.stdout.encode("utf-8")).hexdigest()


def _ledger_or_refuse(repo: str) -> dict | None:
    """Ни одно решение не принимается на основе журнала, который не сошёлся.

    До этой проверки `verify` вызывался ровно из `status` — то есть целостность
    смотрели там, где она ни на что не влияет, и не смотрели там, где на её
    основе принимали решение.
    """
    ok, problems = ledger.verify(repo)
    if not ok:
        return {"error": "ledger hash chain is broken; refusing to act on it",
                "ledger_problems": problems}
    ok, problems = ledger.verify_append_only(repo)
    if not ok:
        return {"error": "ledger disagrees with its committed version; refusing to act on it",
                "ledger_problems": problems}
    return None


def scaffold(repo: str) -> list[str]:
    created = []
    for rel in (".ai-loop", ".ai-loop/policy", ".ai-loop/runs", ".ai-loop/artifacts"):
        path = os.path.join(repo, rel)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            created.append(rel)
    return created


def cmd_start(args) -> int:
    repo = os.path.abspath(args.repo)
    created = scaffold(repo)
    run_id = args.run_id or _run_id(args.idea)
    run_dir = os.path.join(repo, ".ai-loop", "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)

    ledger.append(repo, {
        "run_id": run_id,
        "event": "run_start",
        "phase": "INTAKE",
        "actor": args.actor,
        "idea": args.idea,
        "note": args.note,
    })

    policy_ready = True
    problems: list[str] = []
    try:
        policy_dir = classify_diff.find_policy_dir(repo, None)
        policy = classify_diff.load_policy(policy_dir)
        problems = classify_diff.self_check(policy, repo)
        policy_ready = not problems
    except classify_diff.PolicyError as exc:
        policy_ready = False
        problems = [str(exc)]

    print(json.dumps({
        "run_id": run_id,
        "run_dir": os.path.relpath(run_dir, repo),
        "created": created,
        "idea": args.idea,
        "phase": "INTAKE",
        "control_plane_ready": policy_ready,
        "control_plane_problems": problems,
        "next": "GROUND" if policy_ready else "CONTROL (control plane must exist first)",
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_enter(args) -> int:
    repo = os.path.abspath(args.repo)
    ledger.append(repo, {
        "run_id": args.run_id, "event": "phase_enter",
        "phase": args.phase, "actor": args.actor, "note": args.note,
    })
    print(json.dumps({"run_id": args.run_id, "phase": args.phase, "entered": True}, ensure_ascii=False))
    return 0


def cmd_complete(args) -> int:
    repo = os.path.abspath(args.repo)
    state = ledger.derive_state(repo, args.run_id)
    if state.get("blocked"):
        print(json.dumps({
            "error": "run is blocked on a human decision; resolve it before completing a phase",
            "blocked_on": state.get("blocked_on"),
        }, ensure_ascii=False, indent=2))
        return classify_diff.EXIT_REQUIRE_HUMAN
    if state.get("rejected"):
        # Отказ раньше снимал блок ровно как одобрение, и фаза закрывалась после «нет».
        return _refuse({
            "error": "a gate was rejected; the phase does not complete over a rejection",
            "rejected": state.get("rejected"),
        })
    ledger.append(repo, {
        "run_id": args.run_id, "event": "phase_complete",
        "phase": args.phase, "actor": args.actor, "note": args.note,
        "artifact": args.artifact,
    })
    print(json.dumps({"run_id": args.run_id, "phase": args.phase, "completed": True}, ensure_ascii=False))
    return 0


def cmd_gate(args) -> int:
    repo = os.path.abspath(args.repo)

    broken = _ledger_or_refuse(repo)
    if broken:
        return _refuse(broken)

    argv = ["--repo", repo, "--json", "--run-id", args.run_id]
    if args.phase:
        argv += ["--phase", args.phase]
    if args.cumulative:
        argv.append("--cumulative")
    if args.range:
        argv += ["--range", args.range]
    elif args.staged:
        argv.append("--staged")
    else:
        argv.append("--worktree")

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = classify_diff.main(argv)
    payload = json.loads(buffer.getvalue() or "{}")

    record = {
        "run_id": args.run_id,
        "event": "classification",
        "phase": args.phase,
        "actor": args.actor,
        "risk": payload.get("risk"),
        "gate": payload.get("gate"),
        "decision": payload.get("decision"),
        "paths": [p["path"] if isinstance(p, dict) else p for p in payload.get("paths", [])],
        "policy_fingerprint": payload.get("policy_fingerprint"),
        "base_risk": payload.get("base_risk"),
        "escalations": payload.get("escalations"),
        "files_changed": payload.get("files_changed"),
        "lines_changed": payload.get("lines_changed"),
        "source": payload.get("source"),
        "error": payload.get("error"),
        # Отпечаток того самого диффа, который сейчас классифицирован. Решение
        # человека будет сверено с ним: подпись действительна для снимка, а не
        # для пути в файловой системе.
        "diff_fingerprint": diff_fingerprint(
            repo, staged=bool(args.staged), rng=args.range),
        "diff_source": (f"range:{args.range}" if args.range
                        else "staged" if args.staged else "worktree"),
    }
    ledger.append(repo, {k: v for k, v in record.items() if v not in (None, [], {})})

    verdict = payload.get("decision", "require_human")
    summary = {
        "run_id": args.run_id,
        "phase": args.phase,
        "risk": payload.get("risk"),
        "decision": verdict,
        "files_changed": payload.get("files_changed"),
        "escalations": [e.get("rule") for e in payload.get("escalations", [])],
        "top_reasons": payload.get("reasons", [])[:5],
        "error": payload.get("error"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if verdict != "allow":
        print(
            "\nGATE: human decision required. Show the user the paths and risk above, "
            "then record their answer with:\n"
            f"  loop.py approve --run-id {args.run_id} --actor <name> --note '<why>'\n"
            f"  loop.py reject  --run-id {args.run_id} --actor <name> --note '<why>'",
            file=sys.stderr,
        )
    return code


def _refuse(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return classify_diff.EXIT_ERROR


def _human_decision(args, decision: str) -> int:
    repo = os.path.abspath(args.repo)

    # 1. Актор задаётся явным флагом. AI_LOOP_ACTOR годится агенту для его
    #    собственных записей, но человеческое решение он подменять не должен:
    #    переменная окружения — не подпись.
    if not getattr(args, "actor_explicit", False):
        return _refuse({"error": "a human decision needs an explicit --actor; "
                                 "AI_LOOP_ACTOR from the environment is not a signature"})
    actor = (args.actor or "").strip()
    if not actor or actor.lower() == "agent":
        return _refuse({"error": "a human decision needs a human actor; "
                                 f"--actor {args.actor!r} is not one",
                        "why": "сторона, предлагающая изменение, не может быть стороной, его принимающей"})

    # 2. Целостность журнала — до всего остального.
    broken = _ledger_or_refuse(repo)
    if broken:
        return _refuse(broken)

    state = ledger.derive_state(repo, args.run_id)
    if not state.get("exists"):
        return _refuse({"error": f"run {args.run_id!r} does not exist in this ledger"})

    # 3. Решать можно только то, что открыто.
    open_gates = state.get("blocked_on") or []
    if not open_gates:
        return _refuse({
            "error": "no open gate to decide on",
            "run_id": args.run_id,
            "phase": state.get("phase"),
            "hint": "нечего одобрять: последняя классификация уже разрешена. "
                    "Сначала `loop.py gate`, потом решение по нему.",
        })

    # 4. Какой именно гейт. Молчаливое «последний» и было тем, из-за чего одно
    #    решение закрывало три.
    if args.resolves is None:
        if len(open_gates) > 1:
            return _refuse({
                "error": "several gates are open; name the one you decide with --resolves <seq>",
                "open_gates": [{"seq": g["seq"], "risk": g["risk"], "paths": g["paths"]} for g in open_gates],
            })
        target = open_gates[0]
    else:
        target = next((g for g in open_gates if g["seq"] == args.resolves), None)
        if target is None:
            return _refuse({
                "error": f"gate {args.resolves} is not open",
                "open_gates": [g["seq"] for g in open_gates],
            })

    # 5. Дифф не должен был измениться с момента классификации. Иначе подпись
    #    относится к тому, чего человек не видел. Исключение — `--stale`:
    #    закрытие записи, для которой содержание уже необратимо недоступно
    #    (следующая правка того же файла успела закоммититься и обзавестись
    #    собственной, уже принятой классификацией). Это не «я посмотрел этот
    #    дифф», а «эта запись — мёртвая бухгалтерия, и я это знаю» — потому
    #    ledger хранит их по-разному, и оба пути видны в show/status раздельно.
    recorded = target.get("diff_fingerprint")
    signed = recorded
    if recorded and not args.stale:
        source = target.get("diff_source") or "worktree"
        current = diff_fingerprint(
            repo,
            staged=(source == "staged"),
            rng=(source.split(":", 1)[1] if source.startswith("range:") else None),
        )
        if current != recorded:
            return _refuse({
                "error": "the diff changed since it was classified; the fingerprint no longer matches",
                "gate_seq": target["seq"],
                "classified_fingerprint": recorded,
                "current_fingerprint": current,
                "hint": "переклассифицируй (`loop.py gate`) и решай по свежему диффу — "
                        "подпись действительна только для того снимка, который был показан. "
                        "Если снимок безвозвратно недоступен (правка уже закоммичена под другой "
                        "классификацией) и запись закрывается как мёртвая бухгалтерия, а не как "
                        "разбор содержимого — добавь --stale.",
            })
        signed = current
    elif args.stale and not args.note:
        return _refuse({"error": "--stale closes a record without reviewing its content; "
                                 "--note must say why that is acceptable"})

    record = {
        "run_id": args.run_id,
        "event": "human_decision",
        "phase": state.get("phase"),
        "actor": actor,
        "decision": decision,
        "note": args.note,
        "resolves_seq": target["seq"],
    }
    if args.stale:
        record["stale_closure"] = True
    elif signed:
        record["diff_fingerprint"] = signed
    ledger.append(repo, record)

    print(json.dumps({
        "run_id": args.run_id, "decision": decision, "actor": actor,
        "resolved": target["seq"],
        # Не signed вслепую: при --stale отпечаток не сверялся, и печать здесь
        # не должна намекать на обратное — то же, за что этот флаг и ловил ложь.
        "diff_fingerprint": None if args.stale else signed,
        "stale_closure": bool(args.stale),
        "still_open": [g["seq"] for g in open_gates if g["seq"] != target["seq"]],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    repo = os.path.abspath(args.repo)
    ok, problems = ledger.verify(repo)
    if args.run_id:
        payload = ledger.derive_state(repo, args.run_id)
    else:
        payload = {"runs": ledger.list_runs(repo)}
    payload["ledger_intact"] = ok
    if problems:
        payload["ledger_problems"] = problems
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_finish(args) -> int:
    repo = os.path.abspath(args.repo)
    state = ledger.derive_state(repo, args.run_id)
    if state.get("blocked"):
        print(json.dumps({"error": "cannot finish a run that is blocked on a human decision",
                          "blocked_on": state.get("blocked_on")}, ensure_ascii=False, indent=2))
        return classify_diff.EXIT_REQUIRE_HUMAN
    if state.get("rejected"):
        return _refuse({"error": "cannot finish a run that has a rejected gate",
                        "rejected": state.get("rejected")})
    ledger.append(repo, {
        "run_id": args.run_id, "event": "run_complete", "phase": "DONE",
        "actor": args.actor, "note": args.note, "commit": args.commit,
    })
    print(json.dumps({"run_id": args.run_id, "phase": "DONE"}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--actor", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    def with_actor(p):
        # --actor is accepted after the subcommand as well as before it. Every
        # documented invocation puts it after, and argparse would otherwise reject
        # the form everyone actually types.
        p.add_argument("--actor", default=None)
        return p

    start = with_actor(sub.add_parser("start"))
    start.add_argument("idea")
    start.add_argument("--run-id")
    start.add_argument("--note")
    start.set_defaults(func=cmd_start)

    enter = with_actor(sub.add_parser("enter"))
    enter.add_argument("--run-id", required=True)
    enter.add_argument("--phase", required=True, choices=PHASES)
    enter.add_argument("--note")
    enter.set_defaults(func=cmd_enter)

    complete = with_actor(sub.add_parser("complete"))
    complete.add_argument("--run-id", required=True)
    complete.add_argument("--phase", required=True, choices=PHASES)
    complete.add_argument("--artifact")
    complete.add_argument("--note")
    complete.set_defaults(func=cmd_complete)

    gate = with_actor(sub.add_parser("gate"))
    gate.add_argument("--run-id", required=True)
    gate.add_argument("--phase", choices=PHASES)
    gate.add_argument("--cumulative", action="store_true")
    source = gate.add_mutually_exclusive_group()
    source.add_argument("--range")
    source.add_argument("--staged", action="store_true")
    source.add_argument("--worktree", action="store_true")
    gate.set_defaults(func=cmd_gate)

    approve = with_actor(sub.add_parser("approve"))
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--resolves", type=int,
                         help="seq классификации, которую решает эта подпись")
    approve.add_argument("--stale", action="store_true",
                         help="закрыть запись без сверки диффа — как мёртвую бухгалтерию, "
                              "не как одобрение содержимого; требует --note")
    approve.add_argument("--note")
    approve.set_defaults(func=lambda a: _human_decision(a, "approved"))

    reject = with_actor(sub.add_parser("reject"))
    reject.add_argument("--run-id", required=True)
    reject.add_argument("--resolves", type=int,
                        help="seq классификации, которую решает эта подпись")
    reject.add_argument("--stale", action="store_true",
                        help="закрыть запись без сверки диффа — как мёртвую бухгалтерию, "
                             "не как решение по содержимому; требует --note")
    reject.add_argument("--note")
    reject.set_defaults(func=lambda a: _human_decision(a, "rejected"))

    status = with_actor(sub.add_parser("status"))
    status.add_argument("--run-id")
    status.set_defaults(func=cmd_status)

    finish = with_actor(sub.add_parser("finish"))
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--commit")
    finish.add_argument("--note")
    finish.set_defaults(func=cmd_finish)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Subcommand --actor wins over the global one; fall back to the environment,
    # then to "agent" -- which human_decision explicitly refuses.
    sub_actor = getattr(args, "actor", None)
    # Явно ли актор назван флагом. Человеческое решение принимает только флаг:
    # AI_LOOP_ACTOR в окружении позволял одобрить вообще без него.
    args.actor_explicit = sub_actor is not None
    args.actor = sub_actor or os.environ.get("AI_LOOP_ACTOR") or "agent"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
