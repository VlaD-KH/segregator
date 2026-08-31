#!/usr/bin/env python3
"""Deterministic risk classification for an ai-loop change set.

This is the enforcement layer. It answers exactly one question:

    Given the paths this change actually touches, may the loop continue on its
    own, or must a human decide?

Two properties make it enforcement rather than advice:

1. **The diff is authoritative.** There is no flag by which a caller can declare
   its own zone or risk. Classification reads `git diff --name-only` (or an
   explicit path list) and the policy files -- nothing else. A model that
   believes its change is low risk cannot make it so by saying so.

2. **It fails closed.** A missing policy file, an unparsable policy, a path no
   rule matches, or an internal error all resolve to CRITICAL / require_human.
   The failure mode of a broken control plane is "stop", not "proceed".

Exit codes
    0   allow        -- risk is within the autonomous band
    10  require_human -- a human gate is required before continuing
    2   error        -- treat exactly like require_human (callers must not
                        distinguish; that is the point of failing closed)

Usage
    classify_diff.py --worktree
    classify_diff.py --range main..HEAD --cumulative --run-id 2026-08-15-a
    classify_diff.py --paths src/a.py tests/test_a.py --explain
    classify_diff.py --self-check
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import miniyaml  # noqa: E402

SCHEMA = "ai-loop/classification/v1"
LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
LEVEL_INDEX = {name: i for i, name in enumerate(LEVELS)}

# Most protected first. When a path matches several zones this order decides,
# so that a broad evolvable pattern can never swallow a narrow protected one.
ZONE_PROTECTION_ORDER = ["R", "P", "I", "T", "E"]

EXIT_ALLOW = 0
EXIT_REQUIRE_HUMAN = 10
EXIT_ERROR = 2

# Bookkeeping the loop writes while classifying. Excluded from classification input
# so that the act of recording a decision does not become part of the decision.
DEFAULT_EXCLUSIONS = [
    ".ai-loop/ledger.jsonl",
    ".ai-loop/runs/",
    ".ai-loop/artifacts/",
    ".ai-loop/archive/",
    "**/__pycache__/*",
    "*.pyc",
]


# --------------------------------------------------------------------------- #
# Policy loading
# --------------------------------------------------------------------------- #


class PolicyError(RuntimeError):
    pass


class Policy:
    def __init__(self, protected: dict, risk: dict, fingerprint: str, policy_dir: str):
        self.protected = protected or {}
        self.risk = risk or {}
        self.fingerprint = fingerprint
        self.policy_dir = policy_dir

        enforcement = self.protected.get("enforcement") or {}
        self.diff_is_authoritative = bool(enforcement.get("diff_is_authoritative", True))
        self.mixed_diff_policy = enforcement.get("mixed_diff_policy", "MAX_RISK_WINS")
        self.default_unmatched = _level(enforcement.get("default_risk_for_unmatched", "CRITICAL"))

        # Paths excluded from classification input. Deliberately narrow: these are
        # ai-loop's own bookkeeping, which the act of classifying writes to. The
        # ledger is evidence produced by the gate -- classifying it would mean that
        # recording a decision changes the decision. Its integrity is protected by
        # the hash chain and `ledger.py verify`, not by the risk gate.
        # The defaults hold even if a policy omits the key, so a hand-edited policy
        # cannot accidentally deadlock the loop; a policy may add to them, and any
        # addition is itself a zone P change.
        self.exclusions = list(DEFAULT_EXCLUSIONS) + [
            str(p) for p in (enforcement.get("exclusions") or [])
        ]

        self.zones = self.protected.get("zones") or {}
        self.zone_default_risk = self.risk.get("zone_default_risk") or {}
        self.matrix = self.risk.get("matrix") or []
        self.gate = self.risk.get("gate") or {
            "LOW": "auto",
            "MEDIUM": "auto",
            "HIGH": "require_human",
            "CRITICAL": "require_human",
        }
        self.escalations = self.risk.get("escalations") or {}

    @property
    def version(self) -> str:
        return str(self.protected.get("version", "unknown"))


def _level(name: Any) -> str:
    text = str(name or "").strip().upper()
    if text not in LEVEL_INDEX:
        raise PolicyError(f"unknown risk level: {name!r}")
    return text


def _max_level(a: str, b: str) -> str:
    return a if LEVEL_INDEX[a] >= LEVEL_INDEX[b] else b


def _bump(level: str, steps: int = 1) -> str:
    return LEVELS[min(len(LEVELS) - 1, LEVEL_INDEX[level] + max(0, steps))]


def find_policy_dir(repo: str, explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    for candidate in (
        os.path.join(repo, ".ai-loop", "policy"),
        os.path.join(repo, "evolution", "policy"),
        os.path.join(repo, "policy"),
    ):
        if os.path.isdir(candidate):
            return candidate
    raise PolicyError(
        "no policy directory found (looked for .ai-loop/policy, evolution/policy, policy). "
        "Run the control-plane-init skill before running the loop."
    )


def load_policy(policy_dir: str) -> Policy:
    protected_path = os.path.join(policy_dir, "protected_paths.yaml")
    risk_path = os.path.join(policy_dir, "risk_classification.yaml")
    for path in (protected_path, risk_path):
        if not os.path.isfile(path):
            raise PolicyError(f"missing policy file: {path}")

    digest = hashlib.sha256()
    for path in (protected_path, risk_path):
        with open(path, "rb") as handle:
            digest.update(handle.read())

    try:
        protected = miniyaml.load_path(protected_path)
        risk = miniyaml.load_path(risk_path)
    except Exception as exc:  # noqa: BLE001 - any parse failure must fail closed
        raise PolicyError(f"policy is unparsable ({exc}); refusing to classify") from exc

    if not isinstance(protected, dict) or not isinstance(risk, dict):
        raise PolicyError("policy files must each parse to a mapping")

    return Policy(protected, risk, "sha256:" + digest.hexdigest(), policy_dir)


# --------------------------------------------------------------------------- #
# Path matching
# --------------------------------------------------------------------------- #


def _normalise(path: str) -> str:
    path = str(path).replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _pattern_matches(pattern: str, path: str) -> bool:
    pattern = _normalise(str(pattern))
    if not pattern:
        return False
    if pattern.endswith("/"):
        return path == pattern.rstrip("/") or path.startswith(pattern)
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern + "/*")
    return path == pattern or path.startswith(pattern + "/")


def _specificity(pattern: str) -> int:
    """Literal prefix length before the first wildcard. Longer == more specific."""
    pattern = _normalise(str(pattern))
    for index, ch in enumerate(pattern):
        if ch in "*?[":
            return index
    return len(pattern)


def zone_for(path: str, policy: Policy) -> tuple[str | None, str | None]:
    """Return (zone_key, matched_pattern). Most protected zone wins on overlap."""
    for key in ZONE_PROTECTION_ORDER:
        spec = policy.zones.get(key)
        if not isinstance(spec, dict):
            continue
        best: str | None = None
        for pattern in spec.get("paths") or []:
            if _pattern_matches(pattern, path):
                if best is None or _specificity(pattern) > _specificity(best):
                    best = str(pattern)
        if best is not None:
            return key, best
    # Zones not in the canonical order (custom keys) are checked last, fail-closed
    # in declaration order.
    for key, spec in policy.zones.items():
        if key in ZONE_PROTECTION_ORDER or not isinstance(spec, dict):
            continue
        for pattern in spec.get("paths") or []:
            if _pattern_matches(pattern, path):
                return key, str(pattern)
    return None, None


def matrix_for(path: str, policy: Policy) -> dict | None:
    best: dict | None = None
    best_spec = -1
    for entry in policy.matrix:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("path")
        if pattern and _pattern_matches(pattern, path):
            spec = _specificity(pattern)
            if spec > best_spec:
                best, best_spec = entry, spec
    return best


def is_excluded(path: str, policy: Policy) -> bool:
    path = _normalise(path)
    if "__pycache__/" in path or path.endswith(".pyc"):
        return True
    return any(_pattern_matches(pattern, path) for pattern in policy.exclusions)


def classify_path(path: str, policy: Policy) -> dict:
    path = _normalise(path)
    zone, zone_pattern = zone_for(path, policy)

    if zone is None:
        return {
            "path": path,
            "zone": None,
            "risk": policy.default_unmatched,
            "rule": "default_risk_for_unmatched",
            "note": "no zone rule matched this path; failing closed",
        }

    try:
        risk = _level(policy.zone_default_risk.get(zone, "CRITICAL"))
    except PolicyError:
        risk = "CRITICAL"
    rule = f"zone:{zone}:{zone_pattern}"

    entry = matrix_for(path, policy)
    if entry:
        try:
            entry_risk = _level(entry.get("risk"))
        except PolicyError:
            entry_risk = "CRITICAL"
        # A matrix entry may always raise risk. It may lower risk only inside
        # zones that permit autonomous mutation -- otherwise a fine-grained
        # entry could quietly unprotect a protected zone.
        zone_spec = policy.zones.get(zone) or {}
        mutable_zone = bool(zone_spec.get("autonomous_mutation", False))
        if LEVEL_INDEX[entry_risk] > LEVEL_INDEX[risk] or mutable_zone:
            risk = entry_risk
            rule = f"matrix:{entry.get('path')}"
        else:
            rule = f"zone:{zone}:{zone_pattern} (matrix de-escalation ignored in protected zone)"
        if entry.get("human_approval_required") is True:
            risk = _max_level(risk, "HIGH")
            rule += " +human_approval_required"

    return {"path": path, "zone": zone, "risk": risk, "rule": rule}


# --------------------------------------------------------------------------- #
# Diff collection
# --------------------------------------------------------------------------- #


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PolicyError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def collect_paths(args, repo: str) -> tuple[list[str], str, int]:
    """Return (paths, source_description, lines_changed)."""
    if args.paths:
        return sorted({_normalise(p) for p in args.paths}), "explicit --paths", 0
    if args.paths_file:
        with open(args.paths_file, "r", encoding="utf-8") as handle:
            paths = [_normalise(line.strip()) for line in handle if line.strip()]
        return sorted(set(paths)), f"paths file {args.paths_file}", 0

    if args.range:
        diff_args = ["diff", "--numstat", args.range]
        source = f"git diff {args.range}"
    elif args.staged:
        diff_args = ["diff", "--numstat", "--cached"]
        source = "git diff --cached"
    else:
        diff_args = ["diff", "--numstat", "HEAD"]
        source = "git diff HEAD"

    raw = _git(repo, *diff_args)
    paths: set[str] = set()
    lines_changed = 0
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], parts[-1]
        for count in (added, removed):
            if count.isdigit():
                lines_changed += int(count)
        paths.add(_normalise(path))

    if not args.range and not args.staged:
        # Untracked files are part of the change set even though `git diff` hides them.
        for path in _git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
            if path.strip():
                paths.add(_normalise(path.strip()))
                source = "git diff HEAD + untracked"

    return sorted(paths), source, lines_changed


def cumulative_paths(repo: str, run_id: str | None) -> list[str]:
    """Paths recorded for this run so far, so a series of small allowed steps
    cannot add up to a change the policy would have refused as one step."""
    if not run_id:
        return []
    ledger = os.path.join(repo, ".ai-loop", "ledger.jsonl")
    if not os.path.isfile(ledger):
        return []
    seen: set[str] = set()
    with open(ledger, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("run_id") != run_id:
                continue
            for path in record.get("paths") or []:
                seen.add(_normalise(str(path)))
    return sorted(seen)


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #


def _int_setting(spec: Any, key: str, default: int) -> int:
    if isinstance(spec, dict) and str(spec.get(key, "")).lstrip("-").isdigit():
        return int(spec[key])
    return default


def apply_escalations(base: str, classified: list[dict], policy: Policy,
                      files_changed: int, lines_changed: int) -> tuple[str, list[dict]]:
    risk = base
    applied: list[dict] = []
    esc = policy.escalations

    def record(rule: str, new_risk: str, detail: str) -> None:
        nonlocal risk
        if LEVEL_INDEX[new_risk] > LEVEL_INDEX[risk]:
            applied.append({"rule": rule, "from": risk, "to": new_risk, "detail": detail})
            risk = new_risk

    zones = {item["zone"] for item in classified}

    # Editing a test in the same change as product code bypasses verification:
    # the evidence and the thing it is evidence for move together.
    setting = esc.get("test_and_product_same_diff")
    if setting and "T" in zones and (zones - {"T"}):
        steps = setting if (isinstance(setting, int) and not isinstance(setting, bool)) else 1
        record(
            "test_and_product_same_diff",
            _bump(risk, steps),
            "tests and non-test code changed in the same diff",
        )

    # The agent must never be able to edit what constrains it.
    if "P" in zones:
        record("policy_plane_touched", "CRITICAL", "change touches the policy plane (zone P)")

    unmatched = [item["path"] for item in classified if item["zone"] is None]
    if unmatched:
        record(
            "unmatched_path",
            policy.default_unmatched,
            f"{len(unmatched)} path(s) matched no zone rule: {', '.join(unmatched[:5])}",
        )

    files_spec = esc.get("files_changed_over")
    if isinstance(files_spec, dict):
        threshold = _int_setting(files_spec, "threshold", 10**9)
        if files_changed > threshold:
            record(
                "files_changed_over",
                _bump(risk, _int_setting(files_spec, "bump", 1)),
                f"{files_changed} files changed (threshold {threshold})",
            )

    lines_spec = esc.get("lines_changed_over")
    if isinstance(lines_spec, dict) and lines_changed:
        threshold = _int_setting(lines_spec, "threshold", 10**9)
        if lines_changed > threshold:
            record(
                "lines_changed_over",
                _bump(risk, _int_setting(lines_spec, "bump", 1)),
                f"{lines_changed} lines changed (threshold {threshold})",
            )

    return risk, applied


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #


def self_check(policy: Policy, repo: str) -> list[str]:
    """Structural problems that make a policy decorative rather than enforcing."""
    problems: list[str] = []

    if not policy.diff_is_authoritative:
        problems.append(
            "enforcement.diff_is_authoritative is false: the policy would trust a "
            "self-reported zone, which is the failure this control plane exists to prevent"
        )
    if policy.mixed_diff_policy != "MAX_RISK_WINS":
        problems.append(
            f"enforcement.mixed_diff_policy is {policy.mixed_diff_policy!r}; only MAX_RISK_WINS "
            "is safe for mixed diffs"
        )
    if policy.default_unmatched != "CRITICAL":
        problems.append(
            f"enforcement.default_risk_for_unmatched is {policy.default_unmatched}; anything "
            "below CRITICAL means new files default to permitted"
        )

    for key in ("R", "P"):
        spec = policy.zones.get(key)
        if isinstance(spec, dict) and spec.get("autonomous_mutation"):
            problems.append(f"zone {key} has autonomous_mutation: true; protected zones must not")

    # Non-self-reference: the policy directory itself must be inside a zone that
    # forbids autonomous mutation.
    rel_policy = os.path.relpath(policy.policy_dir, repo)
    probe = _normalise(os.path.join(rel_policy, "protected_paths.yaml"))
    zone, _pattern = zone_for(probe, policy)
    if zone is None or (policy.zones.get(zone) or {}).get("autonomous_mutation"):
        problems.append(
            f"the policy file itself ({probe}) is not covered by a protected zone; "
            "the loop could rewrite its own constraints"
        )

    for name in ("HIGH", "CRITICAL"):
        if policy.gate.get(name) != "require_human":
            problems.append(f"gate.{name} is {policy.gate.get(name)!r}; must be require_human")

    declared = {str(e.get("path")) for e in policy.matrix if isinstance(e, dict)}
    for entry_path in sorted(declared):
        zone, _pattern = zone_for(entry_path, policy)
        if zone is None:
            problems.append(
                f"risk matrix entry {entry_path!r} belongs to no zone; it is unenforceable"
            )

    return problems


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="repository root (default: cwd)")
    parser.add_argument("--policy-dir", default=None, help="override policy directory")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--range", help="revision range, e.g. main..HEAD")
    source.add_argument("--staged", action="store_true", help="classify the staged diff")
    source.add_argument("--worktree", action="store_true", help="classify working tree vs HEAD (default)")
    source.add_argument("--paths", nargs="+", help="classify an explicit path list")
    source.add_argument("--paths-file", help="classify paths listed in a file, one per line")
    parser.add_argument("--cumulative", action="store_true",
                        help="also include every path already recorded for --run-id")
    parser.add_argument("--run-id", help="loop run identifier (used by --cumulative and the ledger)")
    parser.add_argument("--phase", help="loop phase this classification belongs to")
    parser.add_argument("--self-check", action="store_true", help="audit the policy for structural holes")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--explain", action="store_true", help="print the per-path table")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = os.path.abspath(args.repo)

    try:
        policy_dir = find_policy_dir(repo, args.policy_dir)
        policy = load_policy(policy_dir)
    except PolicyError as exc:
        payload = {
            "schema": SCHEMA,
            "risk": "CRITICAL",
            "gate": "require_human",
            "decision": "require_human",
            "error": str(exc),
            "reasons": ["control plane unavailable or unreadable; failing closed"],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_ERROR

    if args.self_check:
        problems = self_check(policy, repo)
        payload = {
            "schema": "ai-loop/self-check/v1",
            "policy_dir": policy_dir,
            "policy_fingerprint": policy.fingerprint,
            "ok": not problems,
            "problems": problems,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_ALLOW if not problems else EXIT_REQUIRE_HUMAN

    try:
        paths, source, lines_changed = collect_paths(args, repo)
    except PolicyError as exc:
        print(json.dumps({
            "schema": SCHEMA, "risk": "CRITICAL", "gate": "require_human",
            "decision": "require_human", "error": str(exc),
        }, indent=2, ensure_ascii=False))
        return EXIT_ERROR

    excluded = [p for p in paths if is_excluded(p, policy)]
    paths = [p for p in paths if not is_excluded(p, policy)]

    step_paths = list(paths)
    if args.cumulative:
        merged = set(paths) | {
            p for p in cumulative_paths(repo, args.run_id) if not is_excluded(p, policy)
        }
        paths = sorted(merged)
        source += f" + cumulative run {args.run_id}"

    classified = [classify_path(path, policy) for path in paths]
    base = "LOW"
    for item in classified:
        base = _max_level(base, item["risk"])
    if not classified:
        base = "LOW"

    risk, escalations = apply_escalations(base, classified, policy, len(paths), lines_changed)
    gate = str(policy.gate.get(risk, "require_human"))
    decision = "allow" if gate == "auto" else "require_human"

    reasons = [f"{item['path']} -> {item['zone'] or '-'} / {item['risk']} ({item['rule']})"
               for item in classified if item["risk"] == risk][:10]

    payload = {
        "schema": SCHEMA,
        "run_id": args.run_id,
        "phase": args.phase,
        "source": source,
        "diff_is_authoritative": policy.diff_is_authoritative,
        "policy_dir": policy_dir,
        "policy_version": policy.version,
        "policy_fingerprint": policy.fingerprint,
        "files_changed": len(step_paths),
        "files_considered": len(paths),
        "excluded": excluded,
        "lines_changed": lines_changed,
        "paths": classified,
        "base_risk": base,
        "escalations": escalations,
        "risk": risk,
        "gate": gate,
        "decision": decision,
        "reasons": reasons,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        if args.explain:
            width = max((len(i["path"]) for i in classified), default=4)
            print("\npath".ljust(width + 2) + "zone  risk      rule", file=sys.stderr)
            for item in classified:
                print(
                    item["path"].ljust(width + 2)
                    + f"{item['zone'] or '-':<6}{item['risk']:<10}{item['rule']}",
                    file=sys.stderr,
                )

    return EXIT_ALLOW if decision == "allow" else EXIT_REQUIRE_HUMAN


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - unexpected failure must still fail closed
        print(json.dumps({
            "schema": SCHEMA, "risk": "CRITICAL", "gate": "require_human",
            "decision": "require_human", "error": f"unexpected failure: {exc}",
        }, indent=2, ensure_ascii=False))
        sys.exit(EXIT_ERROR)
