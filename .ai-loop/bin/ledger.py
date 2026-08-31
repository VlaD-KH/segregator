#!/usr/bin/env python3
"""Append-only, tamper-evident run ledger for ai-loop.

Every phase transition, classification, gate decision and human approval is
appended here as one JSON object per line at `.ai-loop/ledger.jsonl`.

Why a hash chain and not just a log: the ledger is the evidence that a given
change was classified under a given policy and approved by a given actor. A log
that the loop can rewrite proves nothing. Each record carries the SHA-256 of the
previous record, so any edit or deletion breaks verification from that point on.

Commands
    ledger.py append --run-id R --phase BUILD --event classification \
        --risk HIGH --gate require_human --decision require_human \
        --paths a.py b.py --policy-fingerprint sha256:... --note "..."
    ledger.py append-json --run-id R --event classification --stdin
    ledger.py verify
    ledger.py show [--run-id R] [--last N]
    ledger.py runs
    ledger.py state --run-id R
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys

SCHEMA = "ai-loop/ledger/v1"
GENESIS = "0" * 64

PHASES = ["INTAKE", "GROUND", "CONTROL", "DEFINE", "PLAN", "BUILD", "VERIFY", "REVIEW", "SHIP", "DONE"]


def ledger_path(repo: str) -> str:
    return os.path.join(os.path.abspath(repo), ".ai-loop", "ledger.jsonl")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _digest(record: dict) -> str:
    payload = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_records(repo: str) -> list[dict]:
    path = ledger_path(repo)
    if not os.path.isfile(path):
        return []
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"ledger line {number} is not valid JSON: {exc}")
    return records


def append(repo: str, record: dict) -> dict:
    path = ledger_path(repo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = read_records(repo)
    prev = existing[-1]["hash"] if existing else GENESIS

    full = {
        "schema": SCHEMA,
        "seq": len(existing) + 1,
        "ts": _now(),
        "prev": prev,
        **record,
    }
    full["hash"] = _digest(full)

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(full, ensure_ascii=False, sort_keys=True) + "\n")
    return full


def verify(repo: str) -> tuple[bool, list[str]]:
    problems: list[str] = []
    prev = GENESIS
    for index, record in enumerate(read_records(repo), start=1):
        if record.get("seq") != index:
            problems.append(f"record {index}: seq is {record.get('seq')!r}, expected {index}")
        if record.get("prev") != prev:
            problems.append(f"record {index}: prev hash does not match record {index - 1}")
        expected = _digest(record)
        if record.get("hash") != expected:
            problems.append(f"record {index}: content hash mismatch (record was modified)")
        prev = record.get("hash") or GENESIS
    return (not problems), problems


def derive_state(repo: str, run_id: str) -> dict:
    """The run's state is derived from the ledger, never stored separately.

    Two sources of truth for 'what phase are we in' is one source of truth and
    one source of drift.
    """
    records = [r for r in read_records(repo) if r.get("run_id") == run_id]
    if not records:
        return {"run_id": run_id, "exists": False}

    phase = "INTAKE"
    blocked_on: dict | None = None
    approvals: list[dict] = []
    idea = None
    for record in records:
        event = record.get("event")
        if record.get("phase") in PHASES and event in ("phase_enter", "phase_complete"):
            phase = record["phase"]
        if event == "run_start":
            idea = record.get("idea")
        if event in ("classification", "gate") and record.get("decision") == "require_human":
            blocked_on = {
                "seq": record.get("seq"), "phase": record.get("phase"),
                "risk": record.get("risk"), "note": record.get("note"),
                "paths": record.get("paths"),
            }
        if event == "human_decision":
            approvals.append({
                "seq": record.get("seq"), "actor": record.get("actor"),
                "decision": record.get("decision"), "note": record.get("note"),
            })
            if record.get("decision") in ("approved", "rejected"):
                blocked_on = None
        if event == "run_complete":
            phase = "DONE"

    return {
        "run_id": run_id,
        "exists": True,
        "idea": idea,
        "phase": phase,
        "blocked": blocked_on is not None,
        "blocked_on": blocked_on,
        "approvals": approvals,
        "records": len(records),
        "last_ts": records[-1].get("ts"),
    }


def list_runs(repo: str) -> list[dict]:
    runs: dict[str, dict] = {}
    for record in read_records(repo):
        run_id = record.get("run_id")
        if not run_id:
            continue
        entry = runs.setdefault(run_id, {"run_id": run_id, "first_ts": record.get("ts"), "records": 0})
        entry["records"] += 1
        entry["last_ts"] = record.get("ts")
        if record.get("event") == "run_start":
            entry["idea"] = record.get("idea")
    for run_id, entry in runs.items():
        state = derive_state(repo, run_id)
        entry["phase"] = state["phase"]
        entry["blocked"] = state["blocked"]
    return sorted(runs.values(), key=lambda e: e.get("first_ts") or "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("append", help="append one record")
    add.add_argument("--run-id", required=True)
    add.add_argument("--event", required=True,
                     help="run_start | phase_enter | phase_complete | classification | gate | "
                          "human_decision | artifact | note | run_complete")
    add.add_argument("--phase", choices=PHASES)
    add.add_argument("--risk")
    add.add_argument("--gate")
    add.add_argument("--decision")
    add.add_argument("--actor", default=os.environ.get("AI_LOOP_ACTOR", "agent"))
    add.add_argument("--paths", nargs="*", default=[])
    add.add_argument("--commit")
    add.add_argument("--policy-fingerprint")
    add.add_argument("--idea")
    add.add_argument("--artifact")
    add.add_argument("--note")

    addj = sub.add_parser("append-json", help="append a record read from stdin as JSON")
    addj.add_argument("--run-id", required=True)
    addj.add_argument("--event", required=True)
    addj.add_argument("--phase", choices=PHASES)
    addj.add_argument("--actor", default=os.environ.get("AI_LOOP_ACTOR", "agent"))

    sub.add_parser("verify", help="check the hash chain")

    show = sub.add_parser("show", help="print records")
    show.add_argument("--run-id")
    show.add_argument("--last", type=int)

    sub.add_parser("runs", help="list runs")

    state = sub.add_parser("state", help="derive the current state of a run")
    state.add_argument("--run-id", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo

    if args.command == "append":
        record = {
            "run_id": args.run_id,
            "event": args.event,
            "actor": args.actor,
        }
        for key in ("phase", "risk", "gate", "decision", "commit", "idea", "artifact", "note"):
            value = getattr(args, key, None)
            if value:
                record[key] = value
        if args.policy_fingerprint:
            record["policy_fingerprint"] = args.policy_fingerprint
        if args.paths:
            record["paths"] = args.paths
        print(json.dumps(append(repo, record), ensure_ascii=False, indent=2))
        return 0

    if args.command == "append-json":
        raw = sys.stdin.read().strip()
        payload = json.loads(raw) if raw else {}
        record = {"run_id": args.run_id, "event": args.event, "actor": args.actor}
        if args.phase:
            record["phase"] = args.phase
        # Carry through the fields a classification produces, so the ledger and
        # the classifier can never disagree about what was decided.
        for key in ("risk", "gate", "decision", "paths", "policy_fingerprint",
                    "policy_version", "base_risk", "escalations", "files_changed",
                    "lines_changed", "source"):
            if key in payload:
                record[key] = payload[key] if key != "paths" else [
                    p["path"] if isinstance(p, dict) else p for p in payload[key]
                ]
        record["detail"] = {k: v for k, v in payload.items() if k not in record}
        print(json.dumps(append(repo, record), ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify":
        ok, problems = verify(repo)
        print(json.dumps({"ok": ok, "problems": problems}, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    if args.command == "show":
        records = read_records(repo)
        if args.run_id:
            records = [r for r in records if r.get("run_id") == args.run_id]
        if args.last:
            records = records[-args.last:]
        for record in records:
            print(json.dumps(record, ensure_ascii=False))
        return 0

    if args.command == "runs":
        print(json.dumps(list_runs(repo), ensure_ascii=False, indent=2))
        return 0

    if args.command == "state":
        print(json.dumps(derive_state(repo, args.run_id), ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
