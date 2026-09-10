from __future__ import annotations

import json
import sqlite3

import pytest

from lab.client import LLMResponse
from lab.integrity import EvidenceIntegrityError
from lab.research_state import ResearchState
from lab.run_controller import ResearchPaused
from lab.step_store import StepStore
from lab.theorem_engine import TheoremResearchLab
from lab.trace import Trace


def _sql(store: StepStore, query: str, args: tuple = ()) -> None:
    """Edit rows behind the store's back, exactly like a manual SQLite edit."""
    con = sqlite3.connect(store.path)
    try:
        con.execute(query, args)
        con.commit()
    finally:
        con.close()


class _EmptyLiterature:
    def search(self, query, limit=8):
        del query, limit
        return []


class _FakeToolbox:
    def execute(self, request):
        del request
        return None


# --- iteration snapshot: dondurulmuş kayıt korunmalı ve kurcalanınca fail-closed olmalı ---


def test_iteration_snapshot_roundtrip_is_unchanged(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(1, ledger_revision="rev", ledger_context="context", payload={"next_task": "hedef"})

    snapshot = store.get_iteration_snapshot(1)

    assert snapshot is not None
    assert snapshot["iteration"] == 1
    assert snapshot["ledger_revision"] == "rev"
    assert snapshot["ledger_context"] == "context"
    assert snapshot["next_task"] == "hedef"


def test_missing_iteration_snapshot_is_none(tmp_path):
    store = StepStore(tmp_path / "project")

    assert store.get_iteration_snapshot(7) is None


def test_iteration_snapshot_does_not_leak_seal_fields(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(1, ledger_revision="rev", ledger_context="context", payload={"next_task": "hedef"})

    snapshot = store.get_iteration_snapshot(1) or {}

    assert "_evidence_signature" not in snapshot
    assert "_evidence_key_mode" not in snapshot


def test_forged_ledger_context_is_rejected(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(1, ledger_revision="rev", ledger_context="GERÇEK", payload={"next_task": "hedef"})
    _sql(store, "UPDATE iteration_snapshots SET ledger_context=? WHERE iteration=1", ("FORGED LEDGER CONTEXT",))

    with pytest.raises(EvidenceIntegrityError, match="mühür uyuşmazlığı"):
        store.get_iteration_snapshot(1)


def test_forged_snapshot_payload_is_rejected(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(1, ledger_revision="rev", ledger_context="context", payload={"next_task": "hedef"})
    _sql(
        store,
        "UPDATE iteration_snapshots SET payload_json=? WHERE iteration=1",
        (json.dumps({"next_task": "FORGED TASK"}),),
    )

    with pytest.raises(EvidenceIntegrityError):
        store.get_iteration_snapshot(1)


def test_malformed_snapshot_payload_is_fail_closed(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(1, ledger_revision="rev", ledger_context="context", payload={"ok": True})
    _sql(store, "UPDATE iteration_snapshots SET payload_json=? WHERE iteration=1", ("{broken-json",))

    with pytest.raises(EvidenceIntegrityError):
        store.get_iteration_snapshot(1)


def test_unsealed_legacy_snapshot_is_not_served(tmp_path):
    store = StepStore(tmp_path / "project")
    _sql(
        store,
        "INSERT INTO iteration_snapshots(iteration,ledger_revision,ledger_context,payload_json,updated_at)"
        " VALUES(?,?,?,?,?)",
        (3, "rev", "context", json.dumps({"next_task": "hedef"}), "t"),
    )

    with pytest.raises(EvidenceIntegrityError, match="LAB_ADOPT_UNSEALED_CACHE"):
        store.get_iteration_snapshot(3)


def test_update_iteration_payload_keeps_the_seal_valid(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(2, ledger_revision="rev", ledger_context="context", payload={"next_task": "hedef"})

    updated = store.update_iteration_payload(2, proposal={"claim": "C"}, proposal_hash="h")

    assert updated["proposal_hash"] == "h"
    assert updated["next_task"] == "hedef"
    assert updated["ledger_context"] == "context"
    again = store.get_iteration_snapshot(2) or {}
    assert again["proposal"] == {"claim": "C"}


def test_engine_pauses_when_snapshot_seal_is_broken(tmp_path):
    state = ResearchState(tmp_path / "project")
    trace = Trace("seal_scope", out_dir=tmp_path / "runs")
    engine = TheoremResearchLab(trace, state, literature=_EmptyLiterature(), toolbox=_FakeToolbox(), max_retries=1)
    engine._iteration_snapshot(1, "ilk hedef")
    _sql(engine.step_store, "UPDATE iteration_snapshots SET ledger_context=? WHERE iteration=1", ("FORGED",))

    with pytest.raises(ResearchPaused):
        engine._iteration_snapshot(1, "ilk hedef")


# --- partial: kurcalanmış yarım çıktı prompt'a girmemeli ---


def test_partial_roundtrip_is_unchanged(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_partial("iter:1:proposer", {"model": "m", "content": "yarım", "reasoning": "r"})

    partial = store.get_partial("iter:1:proposer")

    assert partial is not None
    assert partial["content"] == "yarım"
    assert "_evidence_signature" not in partial


def test_forged_partial_is_dropped(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_partial("iter:1:proposer", {"model": "m", "content": "yarım"})
    _sql(
        store,
        "UPDATE partials SET payload_json=? WHERE step_key=?",
        (json.dumps({"model": "m", "content": "FORGED PARTIAL"}), "iter:1:proposer"),
    )

    assert store.get_partial("iter:1:proposer") is None


def test_unsealed_legacy_partial_is_dropped(tmp_path):
    store = StepStore(tmp_path / "project")
    _sql(
        store,
        "INSERT INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
        ("iter:1:critic", "fp", json.dumps({"content": "eski yarım"}), "t"),
    )

    assert store.get_partial("iter:1:critic") is None


def test_list_partials_flags_unsealed_rows(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_partial("ok", {"content": "yarım"})
    _sql(
        store,
        "INSERT INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
        ("legacy", "fp", json.dumps({"content": "eski"}), "t"),
    )

    rows = {row["step_key"]: row for row in store.list_partials()}

    assert rows["ok"]["sealed"] is True
    assert rows["legacy"]["sealed"] is False


def test_forged_partial_payload_cannot_fake_the_sealed_flag(tmp_path):
    store = StepStore(tmp_path / "project")
    _sql(
        store,
        "INSERT INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
        ("iter:1:proposer", "fp", json.dumps({"sealed": True, "step_key": "başka", "content": "FORGED"}), "t"),
    )

    row = store.list_partials()[0]

    assert row["sealed"] is False
    assert row["step_key"] == "iter:1:proposer"


def test_snapshot_payload_cannot_shadow_the_frozen_columns(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_iteration_snapshot(
        1,
        ledger_revision="rev",
        ledger_context="context",
        payload={"ledger_context": "gölge", "ledger_revision": "gölge", "next_task": "hedef"},
    )

    snapshot = store.get_iteration_snapshot(1) or {}

    assert snapshot["ledger_context"] == "context"
    assert snapshot["ledger_revision"] == "rev"
    assert snapshot["next_task"] == "hedef"


# --- steps: status/fingerprint sütunları mühürlü payload'dan türetilmeli ---


def test_counts_ignores_forged_status_column(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_step("iter:1:verifier", {"status": "OPEN", "fingerprint": "fp-real"})
    _sql(store, "UPDATE steps SET status='COMPLETE' WHERE step_key=?", ("iter:1:verifier",))

    assert store.counts()["complete_steps"] == 0


def test_list_steps_reports_sealed_payload_values_not_columns(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_step("iter:1:verifier", {"status": "COMPLETE", "fingerprint": "fp-real", "model": "m"})
    _sql(store, "UPDATE steps SET status='FORGED', fingerprint='fp-forged' WHERE step_key=?", ("iter:1:verifier",))

    row = store.list_steps()[0]

    assert row["sealed"] is True
    assert row["status"] == "COMPLETE"
    assert row["fingerprint"] == "fp-real"


def test_counts_and_list_steps_report_unsealed_rows(tmp_path):
    store = StepStore(tmp_path / "project")
    store.put_step("iter:1:verifier", {"status": "COMPLETE", "fingerprint": "fp"})
    _sql(
        store,
        "UPDATE steps SET payload_json=? WHERE step_key=?",
        (json.dumps({"status": "COMPLETE"}), "iter:1:verifier"),
    )

    counts = store.counts()
    row = store.list_steps()[0]

    assert counts["complete_steps"] == 0
    assert counts["unsealed_steps"] == 1
    assert row["sealed"] is False


def test_step_seal_body_format_is_pinned(tmp_path):
    """The existing step seal format must not change; real caches depend on it."""
    store = StepStore(tmp_path / "project")
    payload = {"status": "COMPLETE", "fingerprint": "fp", "result": {"ok": True}}
    store.put_step("iter:1:tool", dict(payload))

    stored = store.get_step("iter:1:tool") or {}
    expected = store.signer.sign("step_cache:v1", {"step_key": "iter:1:tool", "payload": payload})

    assert stored["_evidence_signature"] == expected


# --- migrasyon: eski satırlar sessizce mühürlenmemeli ---


def test_legacy_json_cache_is_not_auto_sealed(tmp_path):
    project = tmp_path / "project"
    project.mkdir(parents=True)
    (project / "step_cache.json").write_text(
        json.dumps({"iter:1:verifier": {"status": "COMPLETE", "fingerprint": "fp", "result": {"verdict": "PASS"}}}),
        encoding="utf-8",
    )

    store = StepStore(project)

    assert store.get_step("iter:1:verifier") is None
    assert store.counts()["unsealed_steps"] == 1


def test_pre_migration_db_row_is_not_auto_sealed(tmp_path):
    project = tmp_path / "project"
    store = StepStore(project)
    _sql(
        store,
        "INSERT INTO steps(step_key,status,fingerprint,payload_json,updated_at) VALUES(?,?,?,?,?)",
        ("iter:1:critic", "COMPLETE", "fp", json.dumps({"status": "COMPLETE", "result": {"decision": "ACCEPT"}}), "t"),
    )

    assert StepStore(project).get_step("iter:1:critic") is None


def test_adoption_requires_an_explicit_opt_in(tmp_path, monkeypatch):
    project = tmp_path / "project"
    store = StepStore(project)
    _sql(
        store,
        "INSERT INTO steps(step_key,status,fingerprint,payload_json,updated_at) VALUES(?,?,?,?,?)",
        ("iter:1:critic", "COMPLETE", "fp", json.dumps({"status": "COMPLETE"}), "t"),
    )
    monkeypatch.delenv("LAB_ADOPT_UNSEALED_CACHE", raising=False)

    reopened = StepStore(project)

    assert reopened.get_step("iter:1:critic") is None
    assert reopened.adoption_record() == {}


def test_adoption_env_seals_legacy_rows_once_and_records_it(tmp_path, monkeypatch):
    project = tmp_path / "project"
    store = StepStore(project)
    _sql(
        store,
        "INSERT INTO steps(step_key,status,fingerprint,payload_json,updated_at) VALUES(?,?,?,?,?)",
        ("iter:1:critic", "COMPLETE", "fp", json.dumps({"status": "COMPLETE"}), "t"),
    )
    _sql(
        store,
        "INSERT INTO partials(step_key,fingerprint,payload_json,updated_at) VALUES(?,?,?,?)",
        ("iter:1:proposer", "fp", json.dumps({"content": "eski yarım"}), "t"),
    )
    monkeypatch.setenv("LAB_ADOPT_UNSEALED_CACHE", "1")

    adopted = StepStore(project)

    assert (adopted.get_step("iter:1:critic") or {})["status"] == "COMPLETE"
    assert (adopted.get_partial("iter:1:proposer") or {})["content"] == "eski yarım"
    record = adopted.adoption_record()
    assert record["steps"] == 1
    assert record["partials"] == 1
    assert record["key_mode"] == adopted.signer.mode
    assert adopted.counts()["adopted_unsealed_rows"] == 2

    # Adoption is a one-time migration: a row planted afterwards stays untrusted.
    _sql(
        adopted,
        "INSERT INTO steps(step_key,status,fingerprint,payload_json,updated_at) VALUES(?,?,?,?,?)",
        ("iter:2:critic", "COMPLETE", "fp", json.dumps({"status": "COMPLETE"}), "t"),
    )
    assert StepStore(project).get_step("iter:2:critic") is None


def test_adoption_never_blesses_a_row_with_a_broken_seal(tmp_path, monkeypatch):
    """Devralma yalnız mühür öncesi satırlar içindir; kurcalanmış mühür devralınmaz."""
    project = tmp_path / "project"
    store = StepStore(project)
    store.put_step("iter:1:verifier", {"status": "COMPLETE", "fingerprint": "fp", "result": {"verdict": "FAIL"}})
    tampered = dict(store.get_step("iter:1:verifier") or {})
    tampered["result"] = {"verdict": "PASS"}
    _sql(
        store,
        "UPDATE steps SET payload_json=? WHERE step_key=?",
        (json.dumps(tampered), "iter:1:verifier"),
    )
    monkeypatch.setenv("LAB_ADOPT_UNSEALED_CACHE", "1")

    adopted = StepStore(project)

    assert adopted.get_step("iter:1:verifier") is None
    assert adopted.counts()["unsealed_steps"] == 1
    assert adopted.adoption_record()["steps"] == 0


def test_store_closes_every_sqlite_connection(tmp_path, monkeypatch):
    import lab.step_store as step_store_module

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        opened.append(con)
        return con

    monkeypatch.setattr(step_store_module.sqlite3, "connect", tracking_connect)
    store = StepStore(tmp_path / "project")
    store.put_step("k", {"status": "COMPLETE"})
    store.get_step("k")
    store.put_partial("k", {"content": "x"})
    store.get_partial("k")
    store.put_iteration_snapshot(1, ledger_revision="r", ledger_context="c", payload={})
    store.get_iteration_snapshot(1)
    store.update_iteration_payload(1, next_task="t")
    store.counts()
    store.list_steps()
    store.list_partials()
    store.clear_partial("k")
    store.delete_step("k")

    assert opened
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")


# --- uçtan uca: kurcalanmış freeze kaydıyla resume PAUSED_ERROR olmalı ---


class _FakeAgent:
    def __init__(self, name: str, output: str, *, fail: bool = False):
        self.name = name
        self.system_prompt = f"system {name}"
        self.model = f"fake/{name}"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.output = output
        self.fail = fail
        self.calls = 0

    def respond(self, messages, stream_callback=None):
        del stream_callback
        self.calls += 1
        if self.fail:
            raise RuntimeError("404 model endpoint missing")
        response = LLMResponse(
            content=self.output,
            model=self.model,
            prompt_tokens=10,
            completion_tokens=5,
            latency_s=0.01,
            cost_usd=0.001,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
        )
        return self.output, response


def _agents(*, critic_fail: bool = False) -> dict[str, _FakeAgent]:
    return {
        "manager": _FakeAgent(
            "ResearchManager", '{"decision":"KEEP","status":"OPEN","reason":"ok","next_task":"next"}'
        ),
        "proposer": _FakeAgent(
            "Theorist",
            '{"title":"T","claim":"C","strategy":"S","evidence_needed":[],"tool_request":{"tool":"none"}}',
        ),
        "critic": _FakeAgent(
            "AdversarialCritic",
            '{"verdict":"KEEP","reason":"no counterexample","counterexample":""}',
            fail=critic_fail,
        ),
        "verifier": _FakeAgent(
            "VerificationEngineer",
            '{"verdict":"PASS","reason":"ok","formal_proof_required":true,"counterexample":""}',
        ),
        "auditor": _FakeAgent("IndependentAuditor", "PASS"),
    }


def _run_once(tmp_path, state, agents, experiment: str) -> str:
    trace = Trace(experiment, out_dir=tmp_path / "runs")
    engine = TheoremResearchLab(
        trace, state, literature=_EmptyLiterature(), toolbox=_FakeToolbox(), max_retries=1
    )
    try:
        return engine.run(
            "frozen problem",
            manager=agents["manager"],
            proposer=agents["proposer"],
            critic=agents["critic"],
            verifier=agents["verifier"],
            auditor=agents["auditor"],
            iterations=1,
            checkpoint_every=99,
        )
    finally:
        trace.close()


def test_resume_with_forged_freeze_record_pauses_before_any_llm_call(tmp_path):
    state = ResearchState(tmp_path / "state")
    first = _agents(critic_fail=True)
    assert "beklemeye alındı" in _run_once(tmp_path, state, first, "first")

    store = StepStore(state.root)
    _sql(store, "UPDATE iteration_snapshots SET ledger_context=? WHERE iteration=1", ("FORGED LEDGER CONTEXT",))

    second = _agents(critic_fail=False)
    result = _run_once(tmp_path, state, second, "second")

    assert "beklemeye alındı" in result
    assert "snapshot mührü doğrulanamadı" in result
    runtime = json.loads((state.root / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["status"] == "PAUSED_ERROR"
    # Kurcalanmış dondurulmuş context hiçbir agent prompt'una girmedi.
    assert second["proposer"].calls == 0
    assert second["critic"].calls == 0
