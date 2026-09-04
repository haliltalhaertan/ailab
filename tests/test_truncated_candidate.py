import json
from pathlib import Path

from lab.client import LLMResponse
from lab.json_io import parse_truncated_object_prefix, repair_instruction
from lab.research_state import ResearchState
from lab.theorem_engine import TheoremResearchLab
from lab.trace import Trace


class EmptyLiterature:
    def search(self, query: str, limit: int = 8):
        return []


class FakeAgent:
    def __init__(self, name: str, responses: list[tuple[str, str]]):
        self.name = name
        self.model = "fake/model"
        self.system_prompt = f"system:{name}"
        self.temperature = 0.0
        self.max_tokens = None
        self.reasoning_effort = None
        self.responses = list(responses)
        self.prompts: list[str] = []

    def respond(self, messages, stream_callback=None):
        self.prompts.append(str(messages[-1].get("content") or ""))
        content, finish_reason = self.responses.pop(0)
        if stream_callback:
            stream_callback("content", content)
        return content, LLMResponse(
            content=content,
            model=self.model,
            prompt_tokens=1,
            completion_tokens=1,
            latency_s=0.0,
            request_messages=[{"role": "system", "content": self.system_prompt}] + messages,
            finish_reason=finish_reason,
        )


def _json_response(value: dict) -> tuple[str, str]:
    return json.dumps(value), "stop"


def test_truncated_prefix_recovers_only_complete_fields():
    raw = '{"title":"Cut","claim":"complete claim","strategy":"complete strategy","tool_request":{"tool":"z3","smt2":"(assert'

    recovered = parse_truncated_object_prefix(raw)

    assert recovered == {
        "title": "Cut",
        "claim": "complete claim",
        "strategy": "complete strategy",
    }
    instruction = repair_instruction(raw, truncated=True)
    assert "Do not guess" in instruction
    assert "Omit an incomplete field entirely" in instruction


def test_truncated_candidate_never_reaches_status_guard_or_tool(tmp_path: Path, monkeypatch):
    state = ResearchState(tmp_path / "state")
    trace = Trace("truncated", out_dir=tmp_path / "runs")
    lab = TheoremResearchLab(trace, state, literature=EmptyLiterature())
    proposer = FakeAgent(
        "Theorist",
        [
            (
                '{"title":"Cut","claim":"complete claim","strategy":"partial strategy","tool_request":{"tool":"tropical_grid","circuit":{',
                "length",
            )
        ],
    )
    verifier = FakeAgent(
        "VerificationEngineer",
        [_json_response({"verdict": "INCONCLUSIVE", "reason": "incomplete", "formal_proof_required": True, "counterexample": ""})],
    )
    critic = FakeAgent(
        "AdversarialCritic",
        [_json_response({"verdict": "KEEP", "reason": "incomplete", "counterexample": ""})],
    )
    manager = FakeAgent(
        "ResearchManager",
        [
            _json_response(
                {
                    "decision": "KEEP",
                    "status": "COMPUTATION_PASS",
                    "reason": "should be ignored",
                    "next_task": "complete candidate",
                    "target_proposal": {},
                }
            )
        ],
    )
    auditor = FakeAgent("IndependentAuditor", [("PASS-WITH-GAPS", "stop")])

    def forbidden_guard(*args, **kwargs):
        raise AssertionError("choose_status must not be called for INCOMPLETE_OUTPUT")

    def forbidden_tool(*args, **kwargs):
        raise AssertionError("tools must not execute for INCOMPLETE_OUTPUT")

    monkeypatch.setattr("lab.theorem_engine.choose_status", forbidden_guard)
    monkeypatch.setattr(lab, "_tool", forbidden_tool)

    report = lab.run(
        "P",
        manager=manager,
        proposer=proposer,
        critic=critic,
        verifier=verifier,
        auditor=auditor,
        iterations=1,
        checkpoint_every=0,
    )
    trace.close()

    item = state.list_items(kind="conjecture")[0]
    assert item.status == "OPEN"
    assert item.metadata["truncated"] is True
    assert item.metadata["completion"] == "INCOMPLETE_OUTPUT"
    assert "INCOMPLETE_OUTPUT" in manager.prompts[0]
    assert "Teorem Araştırması Sonucu" in report
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    event = next(value for value in events if value.get("type") == "incomplete_output_not_promotable")
    assert event["requested"] == "COMPUTATION_PASS"
    assert event["granted"] == "OPEN"
