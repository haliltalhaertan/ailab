from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lab.agent import Agent
from lab.literature import LiteratureClient, Paper
from lab.research_state import ResearchState
from lab.tools import ResearchToolbox, ToolResult
from lab.trace import Trace


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"raw": text}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {"raw": text}
            except json.JSONDecodeError:
                pass
    return {"raw": text}


def _paper_context(papers: list[Paper]) -> str:
    if not papers:
        return "(literature taramasında aday kayıt alınamadı)"
    rows = []
    for i, paper in enumerate(papers, 1):
        authors = ", ".join(paper.authors[:3])
        rows.append(f"{i}. {paper.title} ({paper.year or '?'}) — {authors} — {paper.url}")
    return "\n".join(rows)


@dataclass
class IterationOutcome:
    item_id: str
    decision: str
    status: str
    next_task: str


class TheoremResearchLab:
    """Persistent theorem-research workflow with deterministic verification hooks."""

    def __init__(
        self,
        trace: Trace,
        state: ResearchState,
        literature: LiteratureClient | None = None,
        toolbox: ResearchToolbox | None = None,
    ):
        self.trace = trace
        self.state = state
        self.literature = literature or LiteratureClient()
        self.toolbox = toolbox or ResearchToolbox()

    def _call(self, agent: Agent, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        self.trace.log(
            "agent_start",
            agent=agent.name,
            model=agent.model,
            temperature=agent.temperature,
            prompt=prompt,
        )
        content, response = agent.respond(messages)
        self.trace.agent_call(agent.name, response.model, agent.temperature, messages, response)
        return content

    def _search_literature(self, query: str, limit: int = 8) -> list[Paper]:
        self.trace.log("literature_search_start", query=query, limit=limit)
        try:
            papers = self.literature.search(query, limit=limit)
            self.trace.log(
                "literature_search",
                query=query,
                results=[paper.as_dict() for paper in papers],
            )
            return papers
        except Exception as exc:
            self.trace.log("literature_search_error", query=query, error=str(exc))
            return []

    def _verify_tool_request(self, request: dict[str, Any] | None) -> ToolResult | None:
        if request and str(request.get("tool", "none")).lower() not in {"", "none"}:
            self.trace.log("tool_start", request=request)
        result = self.toolbox.execute(request)
        if result:
            self.trace.log("tool_result", **result.as_dict())
        return result

    def run(
        self,
        problem: str,
        *,
        manager: Agent,
        proposer: Agent,
        critic: Agent,
        verifier: Agent,
        auditor: Agent,
        literature_agent: Agent | None = None,
        iterations: int = 5,
        literature_query: str | None = None,
        checkpoint_every: int = 2,
    ) -> str:
        self.state.freeze_problem(problem)
        self.trace.log("problem_frozen", problem=problem)

        papers = self._search_literature(literature_query or problem)
        literature_context = _paper_context(papers)
        if literature_agent is not None:
            literature_prompt = (
                f"Frozen problem:\n{problem}\n\n"
                f"Candidate literature records:\n{literature_context}\n\n"
                "Bunlar yalnız bibliyografik/snippet adaylarıdır. Problemin bilinen sonuçları, "
                "yakın teoremler, novelty riskleri ve aranması gereken anahtar kelimeler için "
                "kısa bir screening raporu hazırla. Bir yayını okumadıysan theorem içeriğini uydurma."
            )
            literature_report = self._call(literature_agent, literature_prompt)
            known = self.state.add_item(
                "known_result",
                "Literature screening report",
                literature_report,
                status="KNOWN",
                metadata={"screening_only": True},
            )
            self.trace.log(
                "state_change",
                action="create",
                item_id=known.id,
                kind="known_result",
                status="KNOWN",
                title="Literature screening report",
            )
            literature_context += "\n\nLLM LITERATURE SCREEN (not a novelty proof):\n" + literature_report

        next_task = (
            "Problemi daralt; bilinen sınırları ihlal etmeyen, çürütülebilir tek bir lemma, "
            "construction veya lower-bound mekanizması öner."
        )
        outcomes: list[IterationOutcome] = []

        proposal_schema = {
            "title": "...",
            "claim": "...",
            "strategy": "...",
            "evidence_needed": ["..."],
            "tool_request": {
                "tool": "none|script|z3|lean|tropical_grid",
                "name": "",
                "args": [],
                "smt2": "",
                "file": "",
                "circuit": {},
                "weights": [0, 1, 2],
            },
        }

        for iteration in range(1, iterations + 1):
            self.trace.log("iteration_start", iteration=iteration, next_task=next_task)
            state_context = self.state.summary_for_prompt()
            proposal_prompt = (
                f"PROBLEM (frozen):\n{problem}\n\n"
                f"LITERATURE SCREEN:\n{literature_context}\n\n"
                f"RESEARCH LEDGER:\n{state_context}\n\n"
                f"CURRENT TASK:\n{next_task}\n\n"
                "Tek bir araştırma adayı üret. Daha önce FAIL/DROPPED olmuş iddiayı yeniden açma. "
                "Kesin bildiğin ile varsayımı ayır. Çıktıyı SADECE şu JSON şemasında ver:\n"
                + json.dumps(proposal_schema, ensure_ascii=False)
            )
            proposal_raw = self._call(proposer, proposal_prompt)
            proposal = extract_json_object(proposal_raw)
            claim = str(proposal.get("claim") or proposal.get("raw") or "Boş iddia")
            title = str(proposal.get("title") or f"Iteration {iteration} candidate")
            item = self.state.add_item(
                "conjecture",
                title=title,
                claim=claim,
                metadata={"iteration": iteration, "proposal": proposal},
            )
            self.trace.log(
                "state_change",
                action="create",
                item_id=item.id,
                kind="conjecture",
                old_status=None,
                new_status="OPEN",
                title=title,
                claim=claim,
            )

            request = proposal.get("tool_request")
            tool_request = request if isinstance(request, dict) else None
            tool_result = self._verify_tool_request(tool_request)

            verifier_prompt = (
                f"Frozen problem:\n{problem}\n\n"
                f"Candidate {item.id}:\n{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
                "Deterministic tool result:\n"
                + json.dumps(tool_result.as_dict() if tool_result else None, ensure_ascii=False, indent=2)
                + "\n\nBu iddiayı doğrulama mühendisi gibi incele. LLM görüşünü ispat sayma. "
                "Eksik deney, küçük-n testi, SMT kontrolü veya formal proof ihtiyacını belirt. "
                "SADECE JSON ver: "
                '{"verdict":"PASS|FAIL|INCONCLUSIVE","reason":"...",'
                '"formal_proof_required":true,"counterexample":""}'
            )
            verification = extract_json_object(self._call(verifier, verifier_prompt))

            critic_prompt = (
                f"Frozen problem:\n{problem}\n\nCandidate {item.id}:\n{claim}\n\n"
                f"Proposal:\n{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
                "Tool result:\n"
                + json.dumps(tool_result.as_dict() if tool_result else None, ensure_ascii=False, indent=2)
                + f"\n\nVerifier:\n{json.dumps(verification, ensure_ascii=False, indent=2)}\n\n"
                f"Previous ledger:\n{state_context}\n\n"
                "Görevin adayı çürütmek. Gizli varsayım, küçük karşıörnek, asymptotic hata, "
                "yanlış model veya literatürde zaten bilinen sonuç ihtimali ara. "
                "Sonunda açıkça KEEP / REVISE / KILL önerisi ver."
            )
            critique_raw = self._call(critic, critic_prompt)

            manager_prompt = (
                f"Frozen problem:\n{problem}\n\nCandidate {item.id}: {claim}\n\n"
                "Tool:\n"
                + json.dumps(tool_result.as_dict() if tool_result else None, ensure_ascii=False, indent=2)
                + f"\nVerifier:\n{json.dumps(verification, ensure_ascii=False, indent=2)}\n"
                f"Critic:\n{critique_raw}\n\n"
                "Araştırmayı yönet. PROVEN kararı verme: formal checker sonucu yoksa en fazla "
                "PROOF_CANDIDATE. SADECE JSON ver: "
                '{"decision":"KEEP|REVISE|KILL|CHECKPOINT",'
                '"status":"OPEN|COMPUTATION_PASS|PROOF_CANDIDATE|FAIL|DROPPED",'
                '"reason":"...","next_task":"..."}'
            )
            manager_decision = extract_json_object(self._call(manager, manager_prompt))
            decision = str(manager_decision.get("decision", "REVISE")).upper()
            requested_status = str(manager_decision.get("status", "OPEN")).upper()
            if requested_status == "PROVEN":
                requested_status = "PROOF_CANDIDATE"
            allowed = {"OPEN", "COMPUTATION_PASS", "PROOF_CANDIDATE", "FAIL", "DROPPED"}
            status = requested_status if requested_status in allowed else "OPEN"

            counterexample = str(verification.get("counterexample") or "").strip()
            tool_counterexample = bool(
                tool_result
                and tool_result.tool == "tropical_grid"
                and tool_result.metadata.get("status") == "COUNTEREXAMPLE"
            )
            if tool_counterexample:
                counter = self.state.add_counterexample(
                    item.id,
                    "Deterministic tropical grid counterexample",
                    payload=tool_result.metadata,
                )
                self.trace.log(
                    "state_change",
                    action="counterexample",
                    item_id=counter.id,
                    target_id=item.id,
                    kind="counterexample",
                    status="KNOWN",
                    detail=tool_result.metadata,
                )
                status = "FAIL"
            elif verification.get("verdict") == "FAIL" and counterexample:
                counter = self.state.add_counterexample(item.id, counterexample)
                self.trace.log(
                    "state_change",
                    action="counterexample",
                    item_id=counter.id,
                    target_id=item.id,
                    kind="counterexample",
                    status="KNOWN",
                    detail=counterexample,
                )
                status = "FAIL"
            else:
                evidence = [
                    "Verifier: " + str(verification.get("reason", verification)),
                    "Critic: " + critique_raw,
                    "Manager: " + str(manager_decision.get("reason", "")),
                ]
                if tool_result:
                    evidence.append("Tool: " + json.dumps(tool_result.as_dict(), ensure_ascii=False))
                self.state.update_item(item.id, status=status, evidence=evidence)

            self.trace.log(
                "state_change",
                action="status",
                item_id=item.id,
                kind="conjecture",
                old_status="OPEN",
                new_status=status,
                decision=decision,
                reason=str(manager_decision.get("reason", "")),
            )
            next_task = str(manager_decision.get("next_task") or next_task)
            outcomes.append(IterationOutcome(item.id, decision, status, next_task))
            self.trace.log(
                "iteration_end",
                iteration=iteration,
                item_id=item.id,
                decision=decision,
                status=status,
                next_task=next_task,
            )

            if checkpoint_every and iteration % checkpoint_every == 0:
                audit_prompt = (
                    f"Independent audit checkpoint after iteration {iteration}.\n\n"
                    f"Frozen problem:\n{problem}\n\n"
                    f"Ledger:\n{self.state.summary_for_prompt(limit=50)}\n\n"
                    "Aynı araştırmacıların varsayımlarını paylaşma. Özellikle yanlışlıkla kanıt kabul "
                    "edilmiş LLM görüşleri, tekrar açılmış FAIL fikirleri ve novelty risklerini bul. "
                    "Sonucu PASS / PASS-WITH-GAPS / FAIL olarak ver."
                )
                audit = self._call(auditor, audit_prompt)
                audit_item = self.state.add_item(
                    "audit",
                    f"Checkpoint audit {iteration}",
                    audit,
                    status="KNOWN",
                    metadata={"iteration": iteration, "independent": True},
                )
                checkpoint_path = self.state.checkpoint(f"iteration-{iteration}", note=audit[:1000])
                self.trace.log(
                    "checkpoint",
                    iteration=iteration,
                    audit_item_id=audit_item.id,
                    path=str(checkpoint_path),
                    audit=audit,
                )

        final_audit_prompt = (
            f"FINAL INDEPENDENT AUDIT\n\nProblem:\n{problem}\n\n"
            f"Full recent ledger:\n{self.state.summary_for_prompt(limit=100)}\n\n"
            "Hiçbir açık iddiayı ispatlanmış sayma. En güçlü sağ kalan aday, en kritik gap, "
            "en iyi counterexample ve bir sonraki kesin theorem target'ı belirt."
        )
        final_audit = self._call(auditor, final_audit_prompt)
        final_item = self.state.add_item(
            "audit",
            "Final independent audit",
            final_audit,
            status="KNOWN",
            metadata={"independent": True, "final": True},
        )
        checkpoint = self.state.checkpoint("final", note=final_audit[:1500])
        self.trace.log(
            "checkpoint",
            final=True,
            audit_item_id=final_item.id,
            path=str(checkpoint),
            audit=final_audit,
        )

        lines = [
            "# Theorem Research Run",
            "",
            f"Iterations: {iterations}",
            f"Checkpoint: {checkpoint}",
            "",
            "## Outcomes",
        ]
        for outcome in outcomes:
            lines.append(
                f"- {outcome.item_id}: [{outcome.status}] decision={outcome.decision}; next={outcome.next_task}"
            )
        lines.extend(["", "## Final independent audit", final_audit])
        return "\n".join(lines)
