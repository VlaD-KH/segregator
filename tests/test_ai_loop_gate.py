"""Контур контроля ai-loop: подпись человека должна что-то значить.

До этих тестов `.ai-loop/bin/` не был покрыт ничем. Пять дефектов, каждый
проверен на живом коде прежде, чем сюда попасть:

1. Цепочка ledger подделывается: SHA-256 без ключа, `_digest` детерминирован,
   пересчёт всей цепочки из GENESIS проходит `verify` без замечаний. Обрезание
   хвоста тоже. Проверено прогоном на копии настоящего журнала.
2. `verify` вызывается ровно из `status`. Ни gate, ни approve, ни complete не
   смотрят на целостность перед тем, как принять решение на её основе.
3. `blocked_on` — одна переменная-слот. Три подряд выданных гейта закрываются
   одним одобрением.
4. `reject` разблокирует прогон ровно как `approve`: после отказа `complete`
   и `finish` отрабатывают с кодом 0.
5. Одобрение ни к чему не привязано: ни к диффу, ни к классификации, ни к
   версии политики. `resolves_seq` вычисляет сам инструмент.

Плюс: `--actor` проверяется сравнением строки, а `AI_LOOP_ACTOR` в окружении
позволяет одобрить вообще без флага.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / ".ai-loop" / "bin"


def _load(name: str):
    """Загрузить модуль контура: это скрипты, а не пакет."""
    if str(BIN) not in sys.path:
        sys.path.insert(0, str(BIN))
    spec = importlib.util.spec_from_file_location(f"_ailoop_{name}", BIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = _load("ledger")
loop = _load("loop")


@pytest.fixture
def repo(tmp_path):
    """Git-репозиторий с пустым .ai-loop и одним коммитом."""
    root = tmp_path / "repo"
    (root / ".ai-loop").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _commit_ledger(repo: Path, message: str = "ledger") -> None:
    subprocess.run(["git", "add", "-A", ".ai-loop"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)


def _gate_record(repo: Path, run_id: str, seq_note: str, **extra) -> dict:
    record = {
        "run_id": run_id,
        "event": "classification",
        "phase": "BUILD",
        "actor": "agent",
        "risk": "CRITICAL",
        "gate": "require_human",
        "decision": "require_human",
        "paths": ["src/a.py"],
        "note": seq_note,
    }
    record.update(extra)
    return ledger.append(str(repo), record)


def _run(repo: Path, *argv: str) -> int:
    return loop.main(["--repo", str(repo), *argv])


def _start(repo: Path, run_id: str = "R1") -> None:
    ledger.append(str(repo), {
        "run_id": run_id, "event": "run_start", "phase": "INTAKE",
        "actor": "vlad", "idea": "test",
    })


# --- 1. Подделка цепочки -------------------------------------------------------


def test_naive_edit_is_caught_by_the_hash_chain(repo):
    """То, что уже работает: правка записи без пересчёта ломает verify."""
    _start(repo)
    _gate_record(repo, "R1", "гейт")

    path = Path(ledger.ledger_path(str(repo)))
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows[-1]["risk"] = "LOW"
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    ok, problems = ledger.verify(str(repo))
    assert ok is False and problems


def test_recomputed_forgery_of_committed_history_is_caught(repo):
    """Пересчёт всей цепочки из GENESIS проходит хеш-проверку — значит она одна
    ничего не доказывает. Ловить должно сравнение с git: то, что уже
    закоммичено, обязано остаться неизменным префиксом."""
    _start(repo)
    _gate_record(repo, "R1", "гейт")
    ledger.append(str(repo), {
        "run_id": "R1", "event": "human_decision", "phase": "BUILD",
        "actor": "vlad", "decision": "approved", "note": "ok",
    })
    _commit_ledger(repo)

    path = Path(ledger.ledger_path(str(repo)))
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows[-1]["decision"] = "rejected"
    rows[-1]["actor"] = "nikto"
    prev = ledger.GENESIS
    for r in rows:
        r.pop("hash", None)
        r["prev"] = prev
        r["hash"] = ledger._digest(r)
        prev = r["hash"]
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    assert ledger.verify(str(repo))[0] is True, "хеш-цепочка сама по себе подделку не видит"

    ok, problems = ledger.verify_append_only(str(repo))
    assert ok is False, "переписанная закоммиченная запись обязана быть замечена"
    assert any("committed" in p or "закоммич" in p for p in problems)


def test_truncation_of_committed_history_is_caught(repo):
    _start(repo)
    _gate_record(repo, "R1", "гейт")
    _gate_record(repo, "R1", "второй гейт")
    _commit_ledger(repo)

    path = Path(ledger.ledger_path(str(repo)))
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text(rows[0] + "\n", encoding="utf-8")

    ok, problems = ledger.verify_append_only(str(repo))
    assert ok is False, "обрезание закоммиченного хвоста обязано быть замечено"


def test_append_only_check_is_quiet_when_nothing_committed_yet(repo):
    """Свежий репозиторий без коммита журнала — не повод падать."""
    _start(repo)
    ok, problems = ledger.verify_append_only(str(repo))
    assert ok is True, problems


# --- 2. Целостность проверяется перед решением ---------------------------------


def test_approve_refuses_on_a_broken_ledger(repo, capsys):
    _start(repo)
    _gate_record(repo, "R1", "гейт")

    path = Path(ledger.ledger_path(str(repo)))
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows[-1]["risk"] = "LOW"
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    code = _run(repo, "approve", "--run-id", "R1", "--actor", "vlad", "--note", "x")
    assert code != 0, "решение на основе испорченного журнала принимать нельзя"
    assert "ledger" in capsys.readouterr().out.lower()


def test_gate_refuses_on_a_broken_ledger(repo, capsys):
    _start(repo)
    path = Path(ledger.ledger_path(str(repo)))
    path.write_text(path.read_text(encoding="utf-8").replace('"idea":"test"', '"idea":"other"'), encoding="utf-8")

    code = _run(repo, "gate", "--run-id", "R1", "--phase", "BUILD", "--actor", "agent", "--worktree")
    assert code != 0


# --- 3. Одно решение закрывает один гейт ---------------------------------------


def test_one_approval_does_not_close_three_gates(repo):
    _start(repo)
    first = _gate_record(repo, "R1", "гейт 1")
    _gate_record(repo, "R1", "гейт 2")
    _gate_record(repo, "R1", "гейт 3")

    state = ledger.derive_state(str(repo), "R1")
    assert len(state["blocked_on"]) == 3, "открытых гейтов должно быть видно три, а не последний"

    _run(repo, "approve", "--run-id", "R1", "--actor", "vlad",
         "--resolves", str(first["seq"]), "--note", "смотрел дифф гейта 1")

    state = ledger.derive_state(str(repo), "R1")
    assert state["blocked"] is True, "два гейта остались открытыми"
    assert len(state["blocked_on"]) == 2
    assert first["seq"] not in [g["seq"] for g in state["blocked_on"]]


def test_approval_must_name_the_gate_when_several_are_open(repo, capsys):
    _start(repo)
    _gate_record(repo, "R1", "гейт 1")
    _gate_record(repo, "R1", "гейт 2")

    code = _run(repo, "approve", "--run-id", "R1", "--actor", "vlad", "--note", "ok")
    assert code != 0, "при нескольких открытых гейтах надо назвать, какой именно решается"
    assert "resolves" in capsys.readouterr().out.lower()


def test_approval_refused_when_no_gate_is_open(repo, capsys):
    """Ровно то, что случилось у владельца: /ai-approve на неблокированном
    прогоне записывал решение в пустоту."""
    _start(repo)
    code = _run(repo, "approve", "--run-id", "R1", "--actor", "vlad", "--note", "ok")
    assert code != 0
    assert "no open gate" in capsys.readouterr().out.lower()


def test_decision_on_unknown_run_is_refused(repo, capsys):
    code = _run(repo, "approve", "--run-id", "NETU", "--actor", "vlad", "--note", "ok")
    assert code != 0


# --- 4. Отказ останавливает, а не разблокирует ---------------------------------


def test_rejected_gate_stops_the_phase(repo):
    _start(repo)
    gate = _gate_record(repo, "R1", "гейт")
    _run(repo, "reject", "--run-id", "R1", "--actor", "vlad",
         "--resolves", str(gate["seq"]), "--note", "спека неверна")

    state = ledger.derive_state(str(repo), "R1")
    assert state["rejected"], "отказ обязан оставить след, который виден состоянию"

    assert _run(repo, "complete", "--run-id", "R1", "--phase", "BUILD", "--actor", "agent") != 0
    assert _run(repo, "finish", "--run-id", "R1", "--actor", "agent") != 0


def test_approval_after_rejection_of_the_same_gate_reopens_nothing_silently(repo):
    """Передумать можно, но это отдельное решение, а не молчаливая отмена."""
    _start(repo)
    gate = _gate_record(repo, "R1", "гейт")
    _run(repo, "reject", "--run-id", "R1", "--actor", "vlad",
         "--resolves", str(gate["seq"]), "--note", "нет")
    state = ledger.derive_state(str(repo), "R1")
    assert state["rejected"]
    assert len([r for r in state["approvals"] if r["decision"] == "rejected"]) == 1


# --- 5. Подпись привязана к конкретному диффу ----------------------------------


def test_diff_fingerprint_changes_with_the_diff(repo):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    first = loop.diff_fingerprint(str(repo), staged=False)
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    second = loop.diff_fingerprint(str(repo), staged=False)

    assert first and second
    assert first != second, "отпечаток обязан меняться вместе с содержимым диффа"


def test_approval_refused_when_the_worktree_moved_since_classification(repo, capsys):
    """Одобрение действительно для того снимка, который человек видел."""
    _start(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    gate = _gate_record(repo, "R1", "гейт",
                        diff_fingerprint=loop.diff_fingerprint(str(repo), staged=False))

    (repo / "src" / "a.py").write_text("x = 999\n", encoding="utf-8")

    code = _run(repo, "approve", "--run-id", "R1", "--actor", "vlad",
                "--resolves", str(gate["seq"]), "--note", "смотрел")
    assert code != 0, "дифф изменился после классификации — одобрять нечего"
    out = capsys.readouterr().out.lower()
    assert "fingerprint" in out or "изменил" in out


def test_approval_records_the_fingerprint_it_signed(repo):
    _start(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    fp = loop.diff_fingerprint(str(repo), staged=False)
    gate = _gate_record(repo, "R1", "гейт", diff_fingerprint=fp)

    assert _run(repo, "approve", "--run-id", "R1", "--actor", "vlad",
                "--resolves", str(gate["seq"]), "--note", "смотрел дифф") == 0

    last = ledger.read_records(str(repo))[-1]
    assert last["event"] == "human_decision"
    assert last["resolves_seq"] == gate["seq"]
    assert last["diff_fingerprint"] == fp, "в записи должно стоять, ЧТО именно подписано"


def test_own_ledger_append_does_not_invalidate_the_fingerprint(repo):
    """Регресс: ledger.jsonl версионируется, и gate дописывает в него строку
    классификации сразу после того, как посчитал fingerprint. Без исключения
    журнала из diff каждый gate делал бы свой же отпечаток протухшим в момент
    завершения — approve после него падал бы всегда, а не только когда дифф
    действительно изменился.

    Воспроизводится только когда ledger.jsonl уже отслеживается git — ровно
    состояние настоящего репозитория, где журнал закоммичен много ходов назад.
    В свежем тестовом дереве без этого коммита файл для `git diff HEAD`
    невидим (untracked), и баг не проявляется — первая версия этого теста
    молчала именно поэтому."""
    _start(repo)
    _commit_ledger(repo, "ledger tracked from here on")

    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    fp_at_classification = loop.diff_fingerprint(str(repo), staged=False)

    # То, что реально происходит между gate() и approve(): запись в ledger.jsonl.
    ledger.append(str(repo), {"run_id": "R1", "event": "classification", "actor": "agent",
                              "decision": "require_human", "diff_fingerprint": fp_at_classification})

    fp_after_ledger_write = loop.diff_fingerprint(str(repo), staged=False)
    assert fp_after_ledger_write == fp_at_classification, (
        "запись классификации в собственный журнал не должна делать её же отпечаток недействительным"
    )


def test_fingerprint_still_reacts_to_a_real_source_change(repo):
    """Исключение журнала не должно глушить проверку целиком."""
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    before = loop.diff_fingerprint(str(repo), staged=False)

    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    after = loop.diff_fingerprint(str(repo), staged=False)
    assert before != after


def test_gate_stores_a_diff_fingerprint(repo):
    _start(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    _run(repo, "gate", "--run-id", "R1", "--phase", "BUILD", "--actor", "agent", "--staged")

    classifications = [r for r in ledger.read_records(str(repo)) if r.get("event") == "classification"]
    assert classifications, "gate обязан записать классификацию"
    assert classifications[-1].get("diff_fingerprint"), "классификация обязана назвать отпечаток диффа"


# --- --stale: закрытие мёртвой бухгалтерии, а не решение по содержимому -------


def test_stale_closure_output_does_not_print_an_unverified_fingerprint(repo, capsys):
    """Печать не должна намекать на сверку, которой не было — ровно то, что
    сама эта запись существует, чтобы отличать от настоящего approve."""
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    gate = _gate_record(repo, "R1", "гейт", diff_fingerprint=loop.diff_fingerprint(str(repo), staged=False))
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "later fix"], cwd=repo, check=True)

    _run(repo, "approve", "--run-id", "R1", "--resolves", str(gate["seq"]),
         "--actor", "vlad", "--stale", "--note", "снимок недоступен")

    printed = json.loads(capsys.readouterr().out)
    assert printed["diff_fingerprint"] is None
    assert printed["stale_closure"] is True


def test_stale_closure_bypasses_the_fingerprint_check(repo):
    """Ровно случай, который вскрылся на практике: следующая правка того же
    файла успела закоммититься и получить собственную классификацию — снимок
    первой записи безвозвратно недоступен, решать по содержимому уже нечего."""
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    gate = _gate_record(repo, "R1", "гейт", diff_fingerprint=loop.diff_fingerprint(str(repo), staged=False))

    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "later fix"], cwd=repo, check=True)

    assert _run(repo, "approve", "--run-id", "R1", "--resolves", str(gate["seq"]),
                "--actor", "vlad", "--stale",
                "--note", "снимок недоступен, закрыто вместе с изменившим его коммитом") == 0

    last = ledger.read_records(str(repo))[-1]
    assert last["stale_closure"] is True
    assert "diff_fingerprint" not in last, "закрытие без сверки не должно притворяться сверенным"


def test_stale_closure_requires_a_note(repo):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    gate = _gate_record(repo, "R1", "гейт", diff_fingerprint=loop.diff_fingerprint(str(repo), staged=False))

    code = _run(repo, "approve", "--run-id", "R1", "--resolves", str(gate["seq"]),
                "--actor", "vlad", "--stale")
    assert code != 0, "закрытие без ревью содержимого обязано объяснить себя"


def test_non_stale_approve_still_refused_when_diff_moved(repo):
    """--stale не должен становиться обходом по умолчанию."""
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "src/a.py"], cwd=repo, check=True)
    gate = _gate_record(repo, "R1", "гейт", diff_fingerprint=loop.diff_fingerprint(str(repo), staged=False))

    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")

    assert _run(repo, "approve", "--run-id", "R1", "--resolves", str(gate["seq"]),
                "--actor", "vlad", "--note", "ok") != 0


# --- 6. Актор человека задаётся явно -------------------------------------------


def test_env_actor_cannot_stand_in_for_a_human(repo, monkeypatch, capsys):
    """AI_LOOP_ACTOR в окружении не должен превращать агента в человека."""
    _start(repo)
    gate = _gate_record(repo, "R1", "гейт")
    monkeypatch.setenv("AI_LOOP_ACTOR", "vlad")

    code = _run(repo, "approve", "--run-id", "R1", "--resolves", str(gate["seq"]), "--note", "ok")
    assert code != 0, "человеческое решение требует явного --actor, а не переменной окружения"


@pytest.mark.parametrize("actor", ["agent", "", "   ", "Agent", "AGENT"])
def test_agent_shaped_actors_are_refused(repo, actor):
    _start(repo)
    gate = _gate_record(repo, "R1", "гейт")
    assert _run(repo, "approve", "--run-id", "R1", "--actor", actor,
                "--resolves", str(gate["seq"]), "--note", "ok") != 0
