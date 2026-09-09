import json

import pytest

from lab import ResearchState, TheoremResearchLab, Trace
from lab.claim_check import check_claim, normalize_spec, spec_hash
from lab.evidence import evidence_from_tool_result
from lab.run_controller import ResearchPaused
from lab.status_guard import choose_status
from lab.tools import ToolResult
from test_proven_end_to_end import EmptyLiterature, _run


def spec(predicate="n * (n + 1) % 2 == 0", *, maximum=20):
    return {"variables": {"n": {"min": 2, "max": maximum}}, "assumptions": [], "predicate": predicate}


def replay(s, **request):
    return check_claim(s, request, item_id="C-test", claim_hash="a" * 64, iteration=1)


def original_bound():
    return {"variables": {"n": {"min": 2, "max": None}, "k": {"min": 1, "max": 8}},
            "assumptions": [], "predicate": "collatz_steps(n) >= collatz_prefix_lower(residue(n,k),k)"}


def test_original_claim_survives_counterexample_to_stronger_claim():
    original = original_bound()
    result = replay(original, witness={"n": 4, "k": 2})
    assert not result.ok
    assert "satisfies the original predicate" in result.error
    stronger = {**original, "predicate": "collatz_steps(n) > k"}
    counter = replay(stronger, witness={"n": 4, "k": 2})
    assert counter.ok and counter.metadata["kind"] == "DETERMINISTIC_COUNTEREXAMPLE"
    assert spec_hash(original) != spec_hash(stronger)


def test_original_bound_passes_finite_window():
    result = replay(original_bound(), scope={"n": {"min": 2, "max": 60}, "k": {"min": 1, "max": 4}})
    assert result.ok
    assert result.metadata["checked_points"] == 236
    assert result.metadata["exhaustive"] is False


@pytest.mark.parametrize("predicate", ["n*(n+1)%2 == 0", "(n+1)**2-n**2 == 2*n+1",
                                     "n/3+n/3+n/3 == n", "gcd(n,n+1) == 1"])
def test_exact_arithmetic_benchmarks(predicate):
    result = replay(spec(predicate))
    assert result.ok and result.metadata["kind"] == "EXACT_PASS"
    assert result.metadata["exhaustive"]


def test_false_polynomial_has_real_witness():
    result = replay(spec("n**2+n+41 > 0 and (n**2+n+41)%41 != 0", maximum=42))
    assert result.ok
    assert result.metadata["kind"] == "DETERMINISTIC_COUNTEREXAMPLE"
    assert result.metadata["witness"] == {"n": 40}


@pytest.mark.parametrize("check_request", [{"witness": {"n": 1}}, {"witness": {"n": 21}},
                                      {"witness": {"n": True}}, {"witness": {}},
                                      {"scope": {"n": {"min": 2, "max": 10001}}}])
def test_out_of_domain_evidence_rejected(check_request):
    assert not replay(spec(), **check_request).ok


def test_assumptions_are_replayed_and_vacuity_rejected():
    s = spec("n % 2 == 0")
    s["assumptions"] = ["n % 2 == 0"]
    assert not replay(s, witness={"n": 3}).ok
    s["assumptions"] = ["n > 100"]
    assert not replay(s).ok


@pytest.mark.parametrize("predicate", ["__import__('os').system('whoami') == 0", "n.__class__ == 0",
                                      "[x for x in range(n)] == 0", "lambda: n", "n**999999 == 0",
                                      "n / 0 == 1", "collatz_steps(0) > 0", "n > 1.5", "n"])
def test_unsupported_or_unbounded_operations_fail_closed(predicate):
    assert not replay(spec(predicate)).ok


def test_total_work_limit_is_inconclusive(monkeypatch):
    import lab.claim_check as checker
    monkeypatch.setattr(checker, "MAX_WORK", 2)
    result = replay(spec())
    assert not result.ok and result.metadata["kind"] == "INCONCLUSIVE"


def test_request_cannot_replace_frozen_predicate():
    assert not replay(spec(), claim_spec=spec("n < 0")).ok


def test_unbounded_domain_requires_bounded_scope():
    assert not replay(spec(maximum=None)).ok
    result = replay(spec(maximum=None), scope={"n": {"min": 2, "max": 10}})
    assert result.ok and not result.metadata["exhaustive"]


def test_generated_code_success_is_not_claim_validation():
    result = ToolResult(True, "code_experiment", output="PASS", metadata={"successful_run_count": 1})
    decision = choose_status("COMPUTATION_PASS", tool_result=result, verifier={"verdict": "PASS"}, critic={"verdict": "KEEP"})
    assert decision.granted == "OPEN"


def test_operational_witness_cannot_close_unrelated_research_target(tmp_path):
    from test_evidence import _contract
    contract = _contract(tmp_path)
    result = replay(spec("n < 0"))
    evidence = evidence_from_tool_result(result, contract=contract, target_id="T1")
    assert evidence.target_id is None
    assert evidence.resolution_scope == "PARTIAL"
    assert evidence.metadata["operational_claim_only"]
    records = [{"item_id": "C-test", "claim_role": "TARGET_RESOLUTION", "status": "FAIL", "evidence": evidence.as_dict()}]
    assert contract.evaluate_target_transition("T1", records) is None


def test_unknown_model_function_is_not_trusted():
    result = replay(spec("generated_simulation(n) == 1"))
    assert not result.ok
    assert "Unsupported function call" in result.error


@pytest.mark.parametrize("field,bad", [("expected_claim_hash", "wrong"), ("expected_item_id", "C-other"),
                                      ("expected_iteration", 2), ("expected_claim_spec_hash", "wrong")])
def test_checked_evidence_cannot_transfer_between_claims(field, bad):
    s = spec("n < 0")
    result = replay(s)
    binding = dict(expected_item_id="C-test", expected_iteration=1, expected_claim_hash="a"*64,
                   expected_claim_spec_hash=spec_hash(s))
    binding[field] = bad
    guard = choose_status("FAIL", tool_result=result, verifier={"verdict": "FAIL"},
                          critic={"verdict": "KILL"}, evidence=evidence_from_tool_result(result), **binding)
    assert guard.granted == "OPEN"


def test_manager_cannot_override_original_predicate_pass(tmp_path):
    s = spec()
    proposal = {"title": "Even consecutive product", "claim": "For n>=2 consecutive products are even",
                "claim_spec": s, "tool_request": {"tool": "claim_check"}}
    state = ResearchState(tmp_path / "state")
    _, trace = _run(tmp_path, state, proposal)
    item = state.list_items(kind="conjecture")[0]
    assert item.status == "COMPUTATION_PASS"  # fake manager asks for PROVEN
    assert item.metadata["claim_spec"] == normalize_spec(s)
    assert "FROZEN OPERATIONAL CLAIM" in state.research_context()
    assert "MACHINE REPLAY" in state.research_context()
    trace_data = trace.path.read_text(encoding="utf-8")
    assert '"claim_replayed": true' in trace_data


def test_recheck_cannot_change_parent_definition(tmp_path):
    state = ResearchState(tmp_path / "state")
    s = normalize_spec(original_bound())
    parent = state.add_item("conjecture", "Original", "Original", metadata={"claim_spec": s, "claim_spec_hash": spec_hash(s)})
    trace = Trace("recheck", out_dir=tmp_path / "runs")
    try:
        lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
        proposal = {"recheck_item_id": parent.id, "claim_spec": {**s, "predicate": "collatz_steps(n)>k"}}
        with pytest.raises(ResearchPaused, match="changed the original predicate"):
            lab._prepare_operational_claim(proposal)
        unchanged = {"recheck_item_id": parent.id}
        lab._prepare_operational_claim(unchanged)
        assert unchanged["claim_spec"] == s
    finally:
        trace.close()
    assert state.get(parent.id).status == "OPEN"


def test_frozen_spec_cannot_be_overwritten(tmp_path):
    state = ResearchState(tmp_path / "state")
    s = normalize_spec(spec())
    item = state.add_item("conjecture", "Original", "Original", metadata={"claim_spec": s})
    with pytest.raises(ValueError, match="Frozen"):
        state.update_item(item.id, metadata={"claim_spec": spec("n < 0")})


def test_local_error_skips_three_paid_reviews_and_repeated_error_pauses(tmp_path):
    state = ResearchState(tmp_path / "state")
    trace = Trace("local", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
    result = ToolResult(False, "code_experiment", error="Forbidden import: hashlib",
                        metadata={"status": "THEORIST_SOURCE_INVALID"})
    first = state.add_item("conjecture", "first", "first")
    reviews = lab._local_failure_reviews(first, result)
    assert reviews[0]["verdict"] == "INCONCLUSIVE"
    second = state.add_item("conjecture", "second", "second")
    with pytest.raises(ResearchPaused, match="Same local validation failure"):
        lab._local_failure_reviews(second, result)
    trace.close()


def test_final_audit_is_preserved_in_full(tmp_path):
    from test_proven_end_to_end import _agents, _proposal
    state = ResearchState(tmp_path / "state")
    proposal = _proposal()
    proposal["tool_request"] = {"tool": "none"}
    agents = _agents(proposal)
    report = "Review detail. " * 300 + "CRITICAL FINAL FINDING"
    agents["auditor"].outputs = [report]
    trace = Trace("long-review", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
    lab.run("P", iterations=1, checkpoint_every=0, **agents)
    trace.close()
    path = next(state.checkpoint_dir.glob("*_final.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["note"] == report


def test_bad_cheap_model_spec_gets_one_focused_repair(tmp_path):
    from test_proven_end_to_end import _agents
    valid = {"title": "Evenness", "claim": "Even consecutive product", "claim_spec": spec(),
             "tool_request": {"tool": "claim_check"}}
    invalid = {**valid, "claim_spec": spec("unknown(n) > 0")}
    agents = _agents(invalid)
    agents["proposer"].outputs.append(json.dumps(valid))
    state = ResearchState(tmp_path / "state")
    trace = Trace("repair", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
    lab.run("P", iterations=1, checkpoint_every=0, **agents)
    trace.close()
    assert state.list_items(kind="conjecture")[0].status == "COMPUTATION_PASS"
    assert not agents["proposer"].outputs
    assert '"type": "operational_claim_repair"' in trace.path.read_text(encoding="utf-8")


def test_second_bad_spec_stops_before_experiment_or_reviewer_calls(tmp_path):
    from test_proven_end_to_end import _agents
    invalid = {"title": "Bad", "claim": "Bad", "claim_spec": spec("unknown(n) > 0"),
               "tool_request": {"tool": "claim_check"}}
    agents = _agents(invalid)
    agents["proposer"].outputs.append(json.dumps(invalid))
    state = ResearchState(tmp_path / "state")
    trace = Trace("repair-limit", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
    lab.run("P", iterations=1, checkpoint_every=0, **agents)
    assert json.loads((state.root / "runtime.json").read_text(encoding="utf-8"))["status"] == "PAUSED_ERROR"
    trace.close()
    assert not state.list_items(kind="conjecture")
    assert len(agents["verifier"].outputs) == 1
