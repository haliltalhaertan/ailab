from __future__ import annotations

import json
from typing import Any


ROLE_LIBRARY = {
    "ResearchManager": "Araştırmayı yönet; kanıt merdivenini kodun zorladığını varsay. FAIL/DROPPED fikirleri kapalı tut ve tek sonraki görev ver.",
    "Theorist": "Küçük, test edilebilir lemma/construction/lower-bound fikri üret; varsayımı açıkça etiketle ve gerektiğinde deterministic tool iste.",
    "AdversarialCritic": "Adayı çürütmeye çalış: karşıörnek, gizli varsayım, yanlış model, asymptotic hata ve novelty riski ara.",
    "VerificationEngineer": "LLM kanaatini ispat sayma; deterministic evidence ile formal proof gereksinimini kesin ayır. Tool error ile matematiksel counterexample'ı karıştırma.",
    "LiteratureScout": "Literatür/novelty riskini tara; yalnız verilen bibliyografik kayıtların desteklediği şeyi söyle. Boş/başarısız taramayı novelty kanıtı sayma.",
    "IndependentAuditor": "Sıfır-güven bağımsız denetçi ol; OPEN, COMPUTATION_PASS, PROOF_CANDIDATE ve PROVEN basamaklarını kesin ayır.",
}

TOOL_NAMES = ("none", "script", "z3", "lean_draft", "lean", "tropical_grid", "code_experiment")


def proposal_schema() -> dict[str, Any]:
    return {
        "title": "...",
        "claim": "...",
        "strategy": "...",
        "evidence_needed": ["..."],
        "tool_request": {
            "tool": "|".join(TOOL_NAMES),
            "name": "",
            "args": [],
            "smt2": "",
            "file": "",
            "source": "",
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


def proposal_prompt(problem: str, literature: str, ledger: str, next_task: str) -> str:
    return (
        f"PROBLEM (frozen):\n{problem}\n\n"
        f"LITERATURE SCREEN:\n{literature}\n\n"
        f"RESEARCH LEDGER SNAPSHOT (frozen for this iteration):\n{ledger}\n\n"
        f"CURRENT TASK:\n{next_task}\n\n"
        "Produce exactly one research candidate. Do not reopen a FAIL/DROPPED idea listed in the ledger. "
        "Separate proved facts from assumptions. If computation/formal checking is useful, request one tool. "
        "Return ONLY this JSON schema:\n" + json.dumps(proposal_schema(), ensure_ascii=False)
    )


def verifier_prompt(problem: str, item_id: str, proposal: dict, tool_result: dict | None) -> str:
    return (
        f"Frozen problem:\n{problem}\n\nCandidate {item_id}:\n"
        f"{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
        f"Deterministic tool result:\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n\n"
        "Review as a verification engineer. LLM opinion is not proof. Distinguish tool failure from a mathematical counterexample. "
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
        "Try to refute the candidate: hidden assumption, small counterexample, asymptotic mistake, wrong computational model, or known-result risk. "
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
        "Do not claim PROVEN unless a successful formal checker result is explicitly present. Return ONLY JSON: "
        '{"decision":"KEEP|REVISE|KILL|CHECKPOINT","status":"OPEN|COMPUTATION_PASS|PROOF_CANDIDATE|PROVEN|FAIL|DROPPED",'
        '"reason":"...","next_task":"..."}'
    )


def checkpoint_prompt(problem: str, ledger: str, iteration: int, *, final: bool = False) -> str:
    label = "FINAL INDEPENDENT AUDIT" if final else f"INDEPENDENT AUDIT CHECKPOINT AFTER ITERATION {iteration}"
    return (
        f"{label}\n\nFrozen problem:\n{problem}\n\nLedger:\n{ledger}\n\n"
        "Audit with zero trust. Look for LLM opinion being treated as evidence, reopened failed ideas, claim/evidence mismatches, "
        "and novelty overclaims. Return PASS / PASS-WITH-GAPS / FAIL with reasons."
    )
