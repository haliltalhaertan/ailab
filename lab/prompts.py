from __future__ import annotations

import json
from typing import Any

from lab.tool_registry import ToolRegistry


ROLE_LIBRARY = {
    "ResearchManager": "Araştırmayı yönet; kanıt merdivenini kodun zorladığını varsay. FAIL/DROPPED fikirleri kapalı tut ve tek sonraki görev ver.",
    "Theorist": "Küçük, test edilebilir lemma/construction/lower-bound fikri üret; varsayımı açıkça etiketle ve gerektiğinde deterministic tool iste.",
    "AdversarialCritic": "Adayı çürütmeye çalış: karşıörnek, gizli varsayım, yanlış model, asymptotic hata ve novelty riski ara.",
    "VerificationEngineer": "LLM kanaatini ispat sayma; deterministic evidence ile formal proof gereksinimini kesin ayır. Tool error ile matematiksel counterexample'ı karıştırma.",
    "LiteratureScout": "Literatür/novelty riskini tara; yalnız verilen bibliyografik kayıtların desteklediği şeyi söyle. Boş/başarısız taramayı novelty kanıtı sayma.",
    "IndependentAuditor": "Sıfır-güven bağımsız denetçi ol; OPEN, REFUTATION_CANDIDATE, COMPUTATION_PASS, PROOF_CANDIDATE ve PROVEN basamaklarını kesin ayır.",
}


def proposal_schema(registry: ToolRegistry | None = None) -> dict[str, Any]:
    registry = registry or ToolRegistry()
    return {
        "title": "...",
        "claim": "...",
        "strategy": "...",
        "evidence_needed": ["..."],
        "tool_request": {
            "tool": registry.schema_string(),
            "name": "",
            "args": [],
            "smt2": "",
            "file": "",
            "source": "",
            "theorem_name": "",
            "theorem_type": "",
            "circuit": {},
            "weights": [0, 1, 2],
            "task": "",
        },
    }


def literature_prompt(problem: str, context: str, *, reliable: bool) -> str:
    reliability = (
        "The retrieval returned candidate records; this is still only a screening, not a novelty proof."
        if reliable
        else "IMPORTANT: retrieval returned no usable records or failed. This is INCONCLUSIVE, not evidence of novelty. Do not say the result is new because nothing was found."
    )
    return (
        f"Frozen problem:\n{problem}\n\nCandidate literature records:\n{context}\n\n{reliability}\n\n"
        "Prepare a short screening report: known-result risks, nearby work, and better search terms. "
        "Do not invent theorem contents you did not receive."
    )


def proposal_prompt(
    problem: str,
    literature: str,
    ledger: str,
    next_task: str,
    registry: ToolRegistry | None = None,
) -> str:
    return (
        f"PROBLEM (frozen):\n{problem}\n\n"
        f"LITERATURE SCREEN:\n{literature}\n\n"
        f"RESEARCH LEDGER SNAPSHOT (frozen for this iteration):\n{ledger}\n\n"
        f"CURRENT TASK:\n{next_task}\n\n"
        "Produce exactly one research candidate. Do not reopen a FAIL/DROPPED idea listed in the ledger. "
        "A REFUTATION_CANDIDATE is still active: when relevant, prioritize checking its claimed counterexample with a deterministic tool instead of treating it as settled. "
        "Separate proved facts from assumptions. If computation/formal checking is useful, request one tool. "
        "For lean_draft, theorem_name and theorem_type are mandatory and must describe the exact single theorem/lemma in source; "
        "the engine will ignore your filename, bind the source to this iteration's ledger item, and check that exact SHA immediately. "
        "Return ONLY this JSON schema:\n"
        + json.dumps(proposal_schema(registry), ensure_ascii=False)
    )


def verifier_prompt(problem: str, item_id: str, proposal: dict, tool_result: dict | None) -> str:
    return (
        f"Frozen problem:\n{problem}\n\nCandidate {item_id}:\n"
        f"{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
        f"Deterministic tool result:\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n\n"
        "Review as a verification engineer. LLM opinion is not proof. Distinguish tool failure from a mathematical counterexample. "
        "If a formal tool result is present, verify that theorem_type is a faithful formalization of the candidate claim; a compiled unrelated tautology is not evidence for this claim. "
        "Return ONLY JSON: "
        '{"verdict":"PASS|FAIL|INCONCLUSIVE","reason":"...","formal_proof_required":true,"counterexample":""}'
    )


def critic_prompt(
    problem: str,
    item_id: str,
    claim: str,
    proposal: dict,
    tool_result: dict | None,
    verification: dict,
    ledger: str,
) -> str:
    return (
        f"Frozen problem:\n{problem}\n\nCandidate {item_id}:\n{claim}\n\n"
        f"Proposal:\n{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
        f"Tool result:\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n\n"
        f"Verifier:\n{json.dumps(verification, ensure_ascii=False, indent=2)}\n\n"
        f"Frozen previous ledger:\n{ledger}\n\n"
        "Try to refute the candidate: hidden assumption, small counterexample, asymptotic mistake, wrong computational model, known-result risk, "
        "or mismatch between natural-language claim and any supplied formal theorem_type. "
        "Return ONLY JSON: "
        '{"verdict":"KEEP|REVISE|KILL","reason":"...","counterexample":""}'
    )


def manager_prompt(
    problem: str,
    item_id: str,
    claim: str,
    tool_result: dict | None,
    verification: dict,
    critique: dict,
) -> str:
    return (
        f"Frozen problem:\n{problem}\n\nCandidate {item_id}: {claim}\n\n"
        f"Tool:\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n"
        f"Verifier:\n{json.dumps(verification, ensure_ascii=False, indent=2)}\n"
        f"Critic:\n{json.dumps(critique, ensure_ascii=False, indent=2)}\n\n"
        "Choose research direction. Status is a REQUEST only; code-side evidence guards may downgrade it. "
        "An LLM-written counterexample is only a REFUTATION_CANDIDATE until a deterministic tool verifies it; make deterministic verification the next task rather than treating the claim as dead. "
        "Do not claim PROVEN unless a successful same-item bound formal checker result is explicitly present. Return ONLY JSON: "
        '{"decision":"KEEP|REVISE|KILL|CHECKPOINT","status":"OPEN|REFUTATION_CANDIDATE|COMPUTATION_PASS|PROOF_CANDIDATE|PROVEN|FAIL|DROPPED",'
        '"reason":"...","next_task":"..."}'
    )


def checkpoint_prompt(problem: str, ledger: str, iteration: int, *, final: bool = False) -> str:
    label = "FINAL INDEPENDENT AUDIT" if final else f"INDEPENDENT AUDIT CHECKPOINT AFTER ITERATION {iteration}"
    return (
        f"{label}\n\nFrozen problem:\n{problem}\n\nLedger:\n{ledger}\n\n"
        "Audit with zero trust. Look for LLM opinion being treated as evidence, reopened failed ideas, claim/evidence mismatches, "
        "formal statement/claim mismatches, and novelty overclaims. Return PASS / PASS-WITH-GAPS / FAIL with reasons."
    )
