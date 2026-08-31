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
import json
import os
import re
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
    ledger.append(repo, {
        "run_id": args.run_id, "event": "phase_complete",
        "phase": args.phase, "actor": args.actor, "note": args.note,
        "artifact": args.artifact,
    })
    print(json.dumps({"run_id": args.run_id, "phase": args.phase, "completed": True}, ensure_ascii=False))
    return 0


def cmd_gate(args) -> int:
    repo = os.path.abspath(args.repo)

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


def _human_decision(args, decision: str) -> int:
    repo = os.path.abspath(args.repo)
    if args.actor in ("agent", "", None):
        print(json.dumps({
            "error": "a human decision needs a human actor; --actor must not be 'agent'",
        }, ensure_ascii=False, indent=2))
        return classify_diff.EXIT_ERROR
    state = ledger.derive_state(repo, args.run_id)
    ledger.append(repo, {
        "run_id": args.run_id,
        "event": "human_decision",
        "phase": state.get("phase"),
        "actor": args.actor,
        "decision": decision,
        "note": args.note,
        "resolves_seq": (state.get("blocked_on") or {}).get("seq"),
    })
    print(json.dumps({
        "run_id": args.run_id, "decision": decision, "actor": args.actor,
        "resolved": (state.get("blocked_on") or {}).get("seq"),
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
    approve.add_argument("--note")
    approve.set_defaults(func=lambda a: _human_decision(a, "approved"))

    reject = with_actor(sub.add_parser("reject"))
    reject.add_argument("--run-id", required=True)
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
    args.actor = sub_actor or os.environ.get("AI_LOOP_ACTOR") or "agent"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
