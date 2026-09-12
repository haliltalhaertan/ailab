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


def tool_environment_block(registry: ToolRegistry | None) -> str:
    registry = registry or ToolRegistry()
    availability = registry.effective_availability()
    rows = []
    for name in sorted(availability):
        raw = availability[name]
        label = "AÇIK" if raw.get("available") else "KAPALI"
        reason = str(raw.get("reason") or "")
        if name == "script" and raw.get("available"):
            scripts = getattr(registry.toolbox, "scripts", None)
            available_scripts = (
                scripts.available_scripts()
                if scripts is not None and hasattr(scripts, "available_scripts")
                else ()
            )
            listed = ", ".join(available_scripts) if available_scripts else "(yok)"
            reason += f"; checked-in .py dosyaları: {listed}."
            if registry.is_available("code_experiment"):
                reason += " Kendi yazdığın/yeni kod için `script` değil `code_experiment` kullan."
            else:
                reason += " `script` yeni source kabul etmez; yalnız listedeki checked-in dosyaları çalıştırır."
        rows.append(f"- {name}: {label} — {reason}")
    lean_open = bool((availability.get("lean_draft") or {}).get("available"))
    if lean_open:
        lean_rule = (
            "Lean bu koşuda yeni formal doğrulama için kullanılabilir. lean_draft kullanırsan dosyada tam olarak BİR top-level theorem/lemma declaration olsun; "
            "yardımcı lemma gerekiyorsa aynı theorem içinde `have` kullan. İkinci bir `theorem`/`lemma` satırı yazma."
        )
    else:
        lean_rule = (
            "Bu koşuda YENİ formal doğrulama (Lean) çalıştırılamaz; yeni Lean taslağı yazma ve yeni lean_draft isteme. "
            "Ancak aynı run içinde daha önce tamamlanmış, claim-bound ve bütünlük kontrolünden geçen formal evidence tool result olarak yeniden kullanılmışsa "
            "onu sırf runtime Lean şu an kapalı diye geçersiz sayma. Yeni evidence için deterministic Z3/script/checker veya counterexample hedefle."
        )
    return "TOOL AVAILABILITY (run-scoped effective universe):\n" + "\n".join(rows) + "\n" + lean_rule


def proposal_schema(registry: ToolRegistry | None = None) -> dict[str, Any]:
    registry = registry or ToolRegistry()
    return {
        "title": "...",
        "claim": "...",
        "target_id": "",
        "claim_spec": {},
        "recheck_item_id": "",
        "strategy": "...",
        "evidence_needed": ["..."],
        "definitions": {
            "symbol": "def symbol(...):\n    ...",
        },
        "tool_request": {
            "tool": registry.schema_string(available_only=True),
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
            "scope": {},
            "witness": None,
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
    *,
    contract_block: str = "",
    pilot_block: str = "",
) -> str:
    registry = registry or ToolRegistry()
    tools = tool_environment_block(registry)
    contract_rule = (
        "When a frozen research contract is present, target_id MUST be one of the OPEN TARGETS shown in the contract block; "
        "do not invent claim_role because code assigns SUBCLAIM/TARGET_RESOLUTION. "
        if contract_block.strip()
        else ""
    )
    computation_rule = (
        "Numerical trajectories, exhaustive searches, long arithmetic, stopping-time calculations, enumeration, or repetitive symbolic computation "
        "must not be performed manually in reasoning. Specify the computation and delegate it to script/code_experiment or another available deterministic tool. "
    )
    definitions_rule = (
        "For code_experiment, every nontrivial or overloaded symbol used by the experiment MUST appear in top-level `definitions` as short, pure, executable Python. For claim_check, use the built-in functions directly; generated definitions are unnecessary and are not trusted by that checker. "
        "For every Boolean test function use assert test_function(...); never ignore False. "
        'After all checks pass emit one AILAB_TEST={"status":"PASS","checked_points":123} line with the actual checked count. '
        "Do not import sys for exit handling; use assert. Never report PASS after an incomplete or failed test. "
        "The mapping key is the exact symbol name and its value must bind that exact Python name. "
        "CodeExperimentAgent will use the generated definitions.py verbatim and must not reinterpret or redefine these symbols. "
        "When tool_request.source is present, write source that uses these definitions rather than duplicating their semantics. "
    )
    operational_rule = (
        "Prefer claim_check for cheap exact checks without a coding agent. Its claim_spec is the authoritative operational hypothesis: "
        '{"variables":{"n":{"min":2,"max":null}},"assumptions":[],"predicate":"collatz_steps(n) >= ceil_log2(n)"}. '
        "Use integer domains (max=null permits unbounded above); assumptions and predicate are Boolean comparisons with exact rational arithmetic. "
        "Supported functions: abs, min, max, gcd, ceil_log2, collatz_steps, residue(n,k), collatz_prefix_lower(a,k). "
        "collatz_prefix_lower returns the largest prefix length <= k before the representative first reaches 1; residue maps zero to 2**k. "
        "For a check request use scope={n:{min:2,max:100}} (JSON keys quoted), at most 10000 Cartesian points; "
        "for a proposed counterexample use witness={n:4}. Only the engine evaluates the frozen predicate, never generated checker code. "
        "To recheck an earlier operational claim set recheck_item_id to its ledger ID and copy its EXACT claim_spec; "
        "changing definitions, assumptions, domain, relation or bound creates a different hypothesis and cannot refute the old one. "
        "Legacy claims without claim_spec cannot be automatically refuted by this tool. Define a separate operational hypothesis and explicitly review the translation. "
        "If unsupported, use exploratory code_experiment or a reviewed checked-in tool; never fabricate a supported function or a proof. "
        "Generated code execution alone is not a verified claim. Do not use hashlib: the runner already records hashes; print raw structured data. "
    )
    return (
        f"PROBLEM (frozen):\n{problem}\n\n"
        f"LITERATURE SCREEN:\n{literature}\n\n"
        f"RESEARCH LEDGER SNAPSHOT (frozen for this iteration):\n{ledger}\n\n"
        f"CURRENT TASK:\n{next_task}\n"
        f"{contract_block}{pilot_block}\n\n{tools}\n\n"
        "Produce exactly one research candidate. Do not reopen a FAIL/DROPPED idea listed in the ledger. "
        f"{contract_rule}"
        "A REFUTATION_CANDIDATE is still active: when relevant, prioritize checking its claimed counterexample with a deterministic tool instead of treating it as settled. "
        f"{computation_rule}"
        f"{definitions_rule}{operational_rule}"
        "Separate proved facts from assumptions. If computation/formal checking is useful, request ONE tool from the available tool schema only. "
        "Do not describe your own candidate as verified/proven before deterministic evidence exists; call it an aday/candidate. "
        "For lean_draft, theorem_name and theorem_type are mandatory and must describe the exact single theorem/lemma in source; "
        "For a formal claim, claim must be the exact full Lean theorem_type; put the natural-language explanation in strategy. "
        "Natural-language claims stay PROOF_CANDIDATE even if a related formal theorem compiles. "
        "theorem_type must include every declaration parameter and assumption as '∀ <binders>, <type>'; "
        "only a declaration without binders may use its conclusion alone, never a proposition appearing elsewhere in the file; "
        "the engine will ignore your filename, bind the source to this iteration's ledger item, and check that exact SHA immediately. "
        "Return ONLY this JSON schema:\n"
        + json.dumps(proposal_schema(registry), ensure_ascii=False)
    )


def verifier_prompt(
    problem: str,
    item_id: str,
    proposal: dict,
    tool_result: dict | None,
    registry: ToolRegistry | None = None,
) -> str:
    tools = tool_environment_block(registry)
    return (
        f"Frozen problem:\n{problem}\n\nCandidate {item_id}:\n"
        f"{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
        f"Deterministic tool result:\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n\n"
        f"{tools}\n\n"
        "Review as a verification engineer. LLM opinion is not proof. Use these verdicts exactly: "
        "PASS = deterministic evidence actually verifies the candidate claim; "
        "FAIL = deterministic counterexample/refutation establishes that the candidate claim is false; "
        "INCONCLUSIVE = tool unavailable, timeout, syntax/format error, infrastructure failure, or evidence insufficient. "
        "A tool failure is NEVER by itself a mathematical FAIL. "
        "Compare the exact original predicate, domain, assumptions and definitions with the tested condition before judging anything. "
        "A counterexample to sigma>k does not refute sigma>=L when L<=k. A changed predicate requires a new claim. "
        "claim_check replay is authoritative only for its frozen specification and recorded scope; assess prose alignment separately. "
        "A code_experiment success proves execution only; request a reviewed checker for claim validation. "
        "For code_experiment, `tool_result.output` is the canonical raw stdout evidence. "
        "`tool_result.metadata.agent_summary` is an LLM-written convenience summary and is NOT evidence; judge the raw stdout, script hashes, and any parsed AILAB_* trailer instead. "
        "If the summary conflicts with stdout, trust stdout and report the mismatch. "
        "A tropical_grid GRID_PASS establishes only finite-grid functional equality on the tested nonnegative weights; it does NOT establish formal monomial-level provenance polynomial equality. "
        "If a formal tool result is present, verify that theorem_type is a faithful formalization of the candidate claim; a compiled unrelated tautology is not evidence for this claim. "
        "A previously completed claim-bound formal result may remain valid when current Lean execution is unavailable; judge the supplied evidence itself. "
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
        "Replay alleged counterexamples against the EXACT original predicate, including all assumptions. "
        "Separate false, trivial, not new, and unsupported: these are different conclusions. "
        "Do not replace >=L with >k, omit assumptions, or transfer evidence through a self-written target ID. "
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
    *,
    contract_block: str = "",
    registry: ToolRegistry | None = None,
    candidate_incomplete: bool = False,
    repeat_warning: str = "",
) -> str:
    registry = registry or ToolRegistry()
    tools = tool_environment_block(registry)
    lean_open = registry.is_available("lean_draft")
    lean_manager = (
        "Lean açıktır; PROOF_CANDIDATE/PROVEN yine yalnız gerçek bound formal evidence ile istenebilir."
        if lean_open
        else (
            "Yeni Lean çalıştırması kapalıdır: yeni formal doğrulama isteme. PROVEN yalnız Tool alanında daha önce tamamlanmış, "
            "same-item/same-iteration/same-claim bound formal evidence açıkça mevcutsa istenebilir; aksi halde PROVEN isteme."
        )
    )
    incomplete_rule = (
        "IMPORTANT: Bu candidate provider token sınırında kesildi ve INCOMPLETE_OUTPUT olarak işaretlendi. "
        "Yükseltme isteme; status OPEN kalsın. Eksik kısmı olmuş gibi varsayma ve target transition isteme. "
        if candidate_incomplete
        else ""
    )
    repetition_rule = (
        "REPETITION BRAKE: " + repeat_warning.strip() + "\n"
        if repeat_warning.strip()
        else ""
    )
    return (
        f"Frozen problem:\n{problem}\n\nCandidate {item_id}: {claim}\n\n"
        f"Tool:\n{json.dumps(tool_result, ensure_ascii=False, indent=2)}\n"
        f"Verifier:\n{json.dumps(verification, ensure_ascii=False, indent=2)}\n"
        f"Critic:\n{json.dumps(critique, ensure_ascii=False, indent=2)}\n"
        f"{contract_block}\n\n{tools}\n{lean_manager}\n{incomplete_rule}\n{repetition_rule}"
        "DECISION RULES: decision selects the research agenda; status records evidence. KILL plus COMPUTATION_PASS is valid: "
        "retire an unpromising idea while preserving its finite verification. Do not debate these labels. "
        "KILL for lack of novelty is not a mathematical FAIL. Code records agenda_status separately. "
        "claim_check is ONLY a bounded arithmetic predicate evaluator, never an algebraic or formal proof checker. "
        "Do not send prose proofs to it or request unsupported tools. If formal tools are unavailable, leave general claims OPEN. "
        "Keep the requested domain intact. A smaller check is partial progress, not completion of the original task; "
        "record the remaining range explicitly in next_task. Reason and next_task should each be at most three concise sentences. "
        "Choose research direction. Status and target transitions are REQUESTS only; code-side evidence guards may downgrade or reject them. "
        "Keep the original operational predicate immutable. To verify an old hypothesis request recheck_item_id with its exact claim_spec. "
        "A failed stronger statement is not a refutation of a weaker original statement. After a rejected witness, do not order retraction. "
        "An execution-only experiment must stay OPEN until a reviewed checker validates the target. "
        "Prioritize a small exact check before more model discussion. Stop repeated repairs and propose a concrete different check. "
        "An LLM-written counterexample is only a REFUTATION_CANDIDATE until a deterministic tool verifies it; make deterministic verification the next task rather than treating the claim as dead. "
        "Do not claim PROVEN unless a successful same-item bound formal checker result is explicitly present. "
        "If you propose a target transition, put it in target_proposal; code will apply it only when the target-type evidence gate is satisfied. Return ONLY JSON: "
        '{"decision":"KEEP|REVISE|KILL|CHECKPOINT","status":"OPEN|REFUTATION_CANDIDATE|COMPUTATION_PASS|PROOF_CANDIDATE|PROVEN|FAIL|DROPPED",'
        '"reason":"...","next_task":"...","target_proposal":{"target_id":"","status":"CLOSED|FAILED|SUPERSEDED","superseded_by":""}}'
    )


def checkpoint_prompt(
    problem: str,
    ledger: str,
    iteration: int,
    *,
    final: bool = False,
    contract_block: str = "",
) -> str:
    label = "FINAL MODEL REVIEW" if final else f"MODEL REVIEW CHECKPOINT AFTER ITERATION {iteration}"
    return (
        f"{label}\n\nFrozen problem:\n{problem}\n\nLedger:\n{ledger}\n{contract_block}\n\n"
        "Audit with zero trust. Look for LLM opinion being treated as evidence, reopened failed ideas, claim/evidence mismatches, "
        "formal statement/claim mismatches, novelty overclaims, and whether pilot_policy/forbidden_claims are being respected. "
        "This is a model review, not independent mathematical verification; role names do not establish independence. "
        "Report execution success, operational claim validity, and novelty separately. Missing literature never establishes novelty. "
        "Use machine receipts (predicate hash, covered scope, checked witness), not another model summary, for evidence. "
        "Return PASS / PASS-WITH-GAPS / FAIL with reasons."
    )
