from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from lab import ResearchState, TheoremResearchLab, Trace
from lab.client import LLMResponse
from lab.integrity import atomic_write_json, content_fingerprint, sha256_file
from lab.tools import LeanTool


class EmptyLiterature:
    def search(self, query: str, limit: int = 8):
        return []


class FakeAgent:
    def __init__(self, name: str, outputs: list[str]):
        self.name = name
        self.model = "fake/model"
        self.system_prompt = f"system:{name}"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.outputs = list(outputs)

    def respond(self, messages, stream_callback=None):
        content = self.outputs.pop(0)
        if stream_callback:
            stream_callback("content", content)
        return content, LLMResponse(
            content=content,
            model=self.model,
            prompt_tokens=1,
            completion_tokens=1,
            latency_s=0.0,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
        )


def _proposal(claim: str = "The bound claim") -> dict:
    return {
        "title": "Bound theorem",
        "claim": claim,
        "strategy": "formalize",
        "evidence_needed": ["Lean"],
        "tool_request": {
            "tool": "lean_draft",
            "source": "theorem bound : 1 = 1 := by rfl",
            "theorem_name": "bound",
            "theorem_type": "1 = 1",
        },
    }


def _agents(proposal: dict):
    return {
        "manager": FakeAgent(
            "ResearchManager",
            ['{"decision":"KEEP","status":"PROVEN","reason":"formal","next_task":"next"}'],
        ),
        "proposer": FakeAgent("Theorist", [json.dumps(proposal)]),
        "critic": FakeAgent(
            "AdversarialCritic",
            ['{"verdict":"KEEP","reason":"checked","counterexample":""}'],
        ),
        "verifier": FakeAgent(
            "VerificationEngineer",
            ['{"verdict":"PASS","reason":"formal checker passed","formal_proof_required":true,"counterexample":""}'],
        ),
        "auditor": FakeAgent("IndependentAuditor", ["PASS"]),
    }


def _run(tmp_path: Path, state: ResearchState, proposal: dict, *, name: str = "proven"):
    trace = Trace(name, out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
    agents = _agents(proposal)
    lab.run(
        "P",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()
    return lab, trace


def _clean_lean(_self, _candidate):
    return (
        subprocess.CompletedProcess(
            ["lean"],
            0,
            stdout="'bound' does not depend on any axioms",
            stderr="",
        ),
        "lean",
    )


def test_run_can_reach_proven_with_bound_lean_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    monkeypatch.setattr(LeanTool, "_run_lean", _clean_lean)
    state = ResearchState(tmp_path / "state")
    proposal = _proposal()

    _run(tmp_path, state, proposal)

    item = state.list_items(kind="conjecture")[0]
    assert item.status == "PROVEN"
    assert item.metadata["formal_verified"] is True
    assert item.metadata["lean_file"]
    assert item.metadata["lean_sha256"]
    assert item.metadata["claim_hash"] == content_fingerprint("claim:v1", item.claim)
    assert item.metadata["claim_sha256"] == hashlib.sha256(item.claim.encode("utf-8")).hexdigest()
    assert item.metadata["evidence"]["claim_hash"] == content_fingerprint("claim:v1", item.claim)
    assert item.metadata["evidence"]["revision"] == 1


def test_run_rejects_lean_sorry_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")

    def sorry_warning(_self, _candidate):
        return (
            subprocess.CompletedProcess(
                ["lean"],
                0,
                stdout="",
                stderr="warning: declaration uses 'sorry'",
            ),
            "lean",
        )

    monkeypatch.setattr(LeanTool, "_run_lean", sorry_warning)
    state = ResearchState(tmp_path / "state")
    _run(tmp_path, state, _proposal(), name="sorry")

    item = state.list_items(kind="conjecture")[0]
    assert item.status == "OPEN"
    assert "formal_verified" not in item.metadata


def test_run_rejects_cached_formal_evidence_from_different_claim(tmp_path):
    state = ResearchState(tmp_path / "state")
    proposal = _proposal("Current claim")
    proposal_hash = content_fingerprint("proposal:v1", proposal)
    item = state.add_item(
        "conjecture",
        proposal["title"],
        proposal["claim"],
        metadata={"iteration": 1, "proposal": proposal, "proposal_hash": proposal_hash},
    )
    trace = Trace("mismatch", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())

    filename = f"iter-1-{item.id}.lean"
    candidate_dir = state.root / "formal" / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate = candidate_dir / filename
    wrong_hash = content_fingerprint("claim:v1", "Different claim")
    candidate.write_text(
        f"-- ailab-claim: {wrong_hash}\ntheorem bound : 1 = 1 := by rfl\n",
        encoding="utf-8",
    )
    current_hash = content_fingerprint("claim:v1", item.claim)
    current_sha = hashlib.sha256(item.claim.encode("utf-8")).hexdigest()
    request = proposal["tool_request"]
    enriched = {
        "tool": "lean_draft",
        "file": filename,
        "source": request["source"],
        "theorem_name": request["theorem_name"],
        "theorem_type": request["theorem_type"],
        "item_id": item.id,
        "iteration": 1,
        "claim_hash": current_hash,
        "claim_sha256": current_sha,
    }
    fingerprint = content_fingerprint("bound_formal_tool:v2", enriched)
    lab._cache_put(
        "iter:1:tool",
        {
            "status": "COMPLETE",
            "fingerprint": fingerprint,
            "result": {
                "ok": True,
                "tool": "lean",
                "output": "",
                "error": "",
                "metadata": {
                    "formal_verified": True,
                    "source_clean": True,
                    "axioms_verified": True,
                    "formal_binding_verified": True,
                    "file": filename,
                    "lean_sha256": sha256_file(candidate),
                    "theorem_name": "bound",
                    "theorem_type": "1 = 1",
                    "item_id": item.id,
                    "iteration": 1,
                    "claim_hash": wrong_hash,
                    "claim_sha256": current_sha,
                    "axioms": [],
                },
            },
        },
    )
    agents = _agents(proposal)
    lab.run(
        "P",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()

    reread = state.get(item.id)
    assert reread.status == "OPEN"
    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"type": "status_downgraded_by_guard"' in trace_text
    assert '"claim_hash_matches": false' in trace_text


def test_run_without_host_lean_stays_open_without_formal_verified_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_ALLOW_HOST_LEAN", raising=False)

    def should_not_run(_self, _candidate):
        raise AssertionError("host Lean must not execute")

    monkeypatch.setattr(LeanTool, "_run_lean", should_not_run)
    state = ResearchState(tmp_path / "state")
    _run(tmp_path, state, _proposal(), name="disabled")

    item = state.list_items(kind="conjecture")[0]
    assert item.status == "OPEN"
    assert "formal_verified" not in item.metadata


def test_resume_rejects_cached_proof_after_bound_lean_sha_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    monkeypatch.setattr(LeanTool, "_run_lean", _clean_lean)
    state = ResearchState(tmp_path / "state")
    proposal = _proposal()
    _, _ = _run(tmp_path, state, proposal, name="first")
    proven = state.list_items(kind="conjecture")[0]
    assert proven.status == "PROVEN"

    candidate = state.root / "formal" / "candidates" / proven.metadata["lean_file"]
    candidate.write_text(candidate.read_text(encoding="utf-8") + "\n-- changed after proof\n", encoding="utf-8")
    runtime_path = state.root / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime.update({"status": "INTERRUPTED", "completed_iterations": 0})
    atomic_write_json(runtime_path, runtime)

    second_trace = Trace("second", out_dir=tmp_path / "runs")
    second_lab = TheoremResearchLab(second_trace, state, literature=EmptyLiterature())
    agents = _agents(proposal)
    second_lab.run(
        "P",
        manager=agents["manager"],
        proposer=agents["proposer"],
        critic=agents["critic"],
        verifier=agents["verifier"],
        auditor=agents["auditor"],
        iterations=1,
        checkpoint_every=0,
    )
    second_trace.close()

    item = state.get(proven.id)
    assert item.status != "PROVEN"
    assert item.metadata.get("formal_verified") is False
    assert "Stored PROVEN downgraded" in item.metadata.get("integrity_warning", "")


def test_proven_revision_one_cannot_promote_revision_two_without_fresh_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ALLOW_HOST_LEAN", "1")
    monkeypatch.setattr(LeanTool, "_run_lean", _clean_lean)
    state = ResearchState(tmp_path / "state")

    _run(tmp_path, state, _proposal("First claim"), name="revision-one")
    first = state.list_items(kind="conjecture")[0]
    assert first.status == "PROVEN"
    assert first.current_revision == 1
    assert first.metadata["evidence"]["revision"] == 1

    revised_proposal = {
        "title": "Bound theorem revised",
        "claim": "Second claim",
        "revises": first.id,
        "strategy": "revise the statement and require fresh evidence",
        "evidence_needed": ["fresh formal proof"],
        "tool_request": {"tool": "none"},
    }
    trace = Trace("revision-two", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
    lab.run(
        "P",
        manager=FakeAgent(
            "ResearchManager",
            ['{"decision":"KEEP","status":"PROVEN","reason":"try old proof","next_task":"fresh proof"}'],
        ),
        proposer=FakeAgent("Theorist", [json.dumps(revised_proposal)]),
        critic=FakeAgent(
            "AdversarialCritic",
            ['{"verdict":"KEEP","reason":"revision needs fresh evidence","counterexample":""}'],
        ),
        verifier=FakeAgent(
            "VerificationEngineer",
            ['{"verdict":"PASS","reason":"LLM opinion only","formal_proof_required":true,"counterexample":""}'],
        ),
        auditor=FakeAgent("IndependentAuditor", ["PASS"]),
        iterations=2,
        checkpoint_every=0,
    )
    trace.close()

    current = state.get(first.id)
    assert current.current_revision == 2
    assert current.status == "OPEN"
    assert current.revisions[0]["status"] == "PROVEN"
    assert current.revisions[1]["status"] == "OPEN"
    assert current.revisions[0]["claim"] == "First claim"
    assert current.revisions[1]["claim"] == "Second claim"
    assert current.revisions[0]["claim_hash"] != current.revisions[1]["claim_hash"]
    assert "evidence" not in current.metadata
    assert "proof_seal" not in current.metadata
    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"type": "item_revision_added"' in trace_text
    assert '"granted": "OPEN"' in trace_text
