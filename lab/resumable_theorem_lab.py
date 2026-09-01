from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.agent import Agent
from lab.literature import LiteratureClient, Paper
from lab.research_state import ResearchState
from lab.theorem_lab import IterationOutcome, _paper_context, extract_json_object
from lab.tools import ResearchToolbox, ToolResult
from lab.trace import Trace


class ResearchStopped(RuntimeError):
    pass


class ResearchPaused(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if status in {408, 409, 425, 429}:
        return True
    if isinstance(status, int) and 500 <= status <= 599:
        return True
    return any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connection",
            "temporarily unavailable",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
        )
    )


class TheoremResearchLab:
    """Fault-tolerant theorem workflow.

    Durable files live next to ResearchState:
    - runtime.json: current cursor/status/last error
    - step_cache.json: completed LLM/tool step outputs
    - run_config.json: latest agent/model configuration
    - stop.flag: cooperative cancellation request

    A restart with the same project_id reuses completed steps and continues from
    the first incomplete step. An interrupted in-flight API call is repeated,
    while already-completed calls are not charged again.
    """

    def __init__(
        self,
        trace: Trace,
        state: ResearchState,
        literature: LiteratureClient | None = None,
        toolbox: ResearchToolbox | None = None,
        *,
        max_retries: int = 3,
    ):
        self.trace = trace
        self.state = state
        self.literature = literature or LiteratureClient()
        self.toolbox = toolbox or ResearchToolbox()
        self.max_retries = max(1, int(max_retries))
        self.runtime_path = state.root / "runtime.json"
        self.cache_path = state.root / "step_cache.json"
        self.config_path = state.root / "run_config.json"
        self.stop_path = state.root / "stop.flag"

    def _runtime(self) -> dict[str, Any]:
        return _read_json(
            self.runtime_path,
            {
                "status": "NEW",
                "completed_iterations": 0,
                "next_task": "",
                "current_iteration": 0,
                "current_step": "",
                "last_error": "",
            },
        )

    def _set_runtime(self, **updates: Any) -> dict[str, Any]:
        value = self._runtime()
        value.update(updates)
        value["updated_at"] = _now()
        _atomic_json(self.runtime_path, value)
        self.trace.log("runtime_state", **value)
        return value

    def _cache(self) -> dict[str, Any]:
        return _read_json(self.cache_path, {})

    def _cache_get(self, key: str) -> Any | None:
        return self._cache().get(key)

    def _cache_put(self, key: str, value: Any) -> None:
        cache = self._cache()
        cache[key] = value
        _atomic_json(self.cache_path, cache)

    def _check_stop(self) -> None:
        if self.stop_path.exists():
            raise ResearchStopped("Kullanıcı durdurma isteği gönderdi.")

    def _stream_callback(self, agent: Agent, step_key: str):
        def callback(channel: str, delta: Any) -> None:
            self._check_stop()
            self.trace.log(
                "agent_stream",
                agent=agent.name,
                model=agent.model,
                step_key=step_key,
                channel=channel,
                delta=delta,
            )

        return callback

    def _call(self, agent: Agent, prompt: str, step_key: str) -> str:
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            self.trace.log(
                "step_reused",
                step_key=step_key,
                agent=agent.name,
                model=cached.get("model"),
            )
            return str(cached.get("content", ""))

        self._check_stop()
        self._set_runtime(current_step=step_key)
        messages = [{"role": "user", "content": prompt}]

        for attempt in range(1, self.max_retries + 1):
            self.trace.log(
                "agent_start",
                agent=agent.name,
                model=agent.model,
                temperature=agent.temperature,
                system_prompt=agent.system_prompt,
                prompt=prompt,
                step_key=step_key,
                attempt=attempt,
            )
            try:
                content, response = agent.respond(
                    messages,
                    stream_callback=self._stream_callback(agent, step_key),
                )
                self.trace.agent_call(
                    agent.name,
                    response.model,
                    agent.temperature,
                    messages,
                    response,
                )
                self._cache_put(
                    step_key,
                    {
                        "status": "COMPLETE",
                        "content": content,
                        "model": response.model,
                        "completed_at": _now(),
                    },
                )
                return content
            except ResearchStopped:
                raise
            except Exception as exc:
                retry = _retryable(exc) and attempt < self.max_retries
                self.trace.log(
                    "agent_error",
                    agent=agent.name,
                    model=agent.model,
                    prompt=prompt,
                    step_key=step_key,
                    attempt=attempt,
                    retrying=retry,
                    error=repr(exc),
                )
                if not retry:
                    raise ResearchPaused(
                        f"{agent.name} / {agent.model} adımında hata: {exc}"
                    ) from exc
                wait_s = min(2 ** (attempt - 1), 8)
                self.trace.log(
                    "agent_retry",
                    agent=agent.name,
                    step_key=step_key,
                    next_attempt=attempt + 1,
                    wait_s=wait_s,
                )
                time.sleep(wait_s)
                self._check_stop()
        raise ResearchPaused(f"{step_key} tamamlanamadı")

    def _tool(self, request: dict[str, Any] | None, step_key: str) -> ToolResult | None:
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            raw = cached.get("result")
            if isinstance(raw, dict):
                self.trace.log("step_reused", step_key=step_key, tool=raw.get("tool"))
                return ToolResult(
                    bool(raw.get("ok")),
                    str(raw.get("tool", "unknown")),
                    str(raw.get("output", "")),
                    str(raw.get("error", "")),
                    dict(raw.get("metadata", {}) or {}),
                )
            return None

        self._check_stop()
        self._set_runtime(current_step=step_key)
        if request and str(request.get("tool", "none")).lower() not in {"", "none"}:
            self.trace.log("tool_start", request=request, step_key=step_key)
        result = self.toolbox.execute(request)
        if result:
            self.trace.log("tool_result", step_key=step_key, **result.as_dict())
        self._cache_put(
            step_key,
            {
                "status": "COMPLETE",
                "result": result.as_dict() if result else None,
                "completed_at": _now(),
            },
        )
        return result

    def _search_literature(self, query: str, limit: int = 8) -> list[Paper]:
        key = "literature:search"
        cached = self._cache_get(key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            papers = []
            for raw in cached.get("papers", []):
                try:
                    papers.append(Paper(**raw))
                except TypeError:
                    continue
            self.trace.log("step_reused", step_key=key, records=len(papers))
            return papers

        self._check_stop()
        self.trace.log("literature_search_start", query=query, limit=limit)
        try:
            papers = self.literature.search(query, limit=limit)
            payload = [paper.as_dict() for paper in papers]
            self.trace.log("literature_search", query=query, results=payload)
            self._cache_put(
                key,
                {"status": "COMPLETE", "papers": payload, "completed_at": _now()},
            )
            return papers
        except Exception as exc:
            self.trace.log("literature_search_error", query=query, error=str(exc))
            # Literature discovery is useful but should not make the run unrecoverable.
            return []

    def _save_config(
        self,
        problem: str,
        iterations: int,
        literature_query: str | None,
        checkpoint_every: int,
        agents: dict[str, Agent],
    ) -> None:
        payload = {
            "problem": problem,
            "iterations": int(iterations),
            "literature_query": literature_query,
            "checkpoint_every": int(checkpoint_every),
            "agents": {
                role: {
                    "name": agent.name,
                    "system_prompt": agent.system_prompt,
                    "model": agent.model,
                    "temperature": agent.temperature,
                    "max_tokens": agent.max_tokens,
                }
                for role, agent in agents.items()
            },
            "saved_at": _now(),
        }
        _atomic_json(self.config_path, payload)

    def _iteration_item(self, iteration: int):
        candidates = [
            item
            for item in self.state.list_items(kind="conjecture")
            if int(item.metadata.get("iteration", -1)) == iteration
        ]
        return candidates[-1] if candidates else None

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
        agents = {
            "ResearchManager": manager,
            "Theorist": proposer,
            "AdversarialCritic": critic,
            "VerificationEngineer": verifier,
            "IndependentAuditor": auditor,
        }
        if literature_agent is not None:
            agents["LiteratureScout"] = literature_agent
        self._save_config(problem, iterations, literature_query, checkpoint_every, agents)

        # A fresh click after a prior STOP means resume, unless another tab writes
        # stop.flag again while this run is active.
        if self.stop_path.exists():
            self.stop_path.unlink(missing_ok=True)

        try:
            return self._run_inner(
                problem,
                manager=manager,
                proposer=proposer,
                critic=critic,
                verifier=verifier,
                auditor=auditor,
                literature_agent=literature_agent,
                iterations=iterations,
                literature_query=literature_query,
                checkpoint_every=checkpoint_every,
            )
        except ResearchStopped as exc:
            self._set_runtime(status="STOPPED", last_error=str(exc))
            self.trace.log("run_stopped", error=str(exc))
            return (
                "# Araştırma durduruldu\n\n"
                "Kalıcı state ve tamamlanan adımlar kaydedildi. Aynı `project_id` ile yeniden "
                "çalıştırıldığında ilk tamamlanmamış adımdan devam eder."
            )
        except ResearchPaused as exc:
            self._set_runtime(status="PAUSED_ERROR", last_error=str(exc))
            self.trace.log("run_paused", error=str(exc))
            return (
                "# Araştırma hata nedeniyle beklemeye alındı\n\n"
                f"{exc}\n\n"
                "Önce hatayı/model seçimini düzelt. Aynı `project_id` ile tekrar çalıştır; "
                "tamamlanmış prompt/response ve tool adımları tekrar çalıştırılmadan devam edilir."
            )

    def _run_inner(
        self,
        problem: str,
        *,
        manager: Agent,
        proposer: Agent,
        critic: Agent,
        verifier: Agent,
        auditor: Agent,
        literature_agent: Agent | None,
        iterations: int,
        literature_query: str | None,
        checkpoint_every: int,
    ) -> str:
        self.state.freeze_problem(problem)
        self.trace.log("problem_frozen", problem=problem)
        runtime = self._runtime()
        completed = int(runtime.get("completed_iterations", 0) or 0)
        next_task = str(runtime.get("next_task") or "").strip() or (
            "Problemi daralt; bilinen sınırları ihlal etmeyen, çürütülebilir tek bir lemma, "
            "construction veya lower-bound mekanizması öner."
        )
        self._set_runtime(status="RUNNING", last_error="")

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
            literature_report = self._call(
                literature_agent, literature_prompt, "literature:agent"
            )
            existing_known = [
                x
                for x in self.state.list_items(kind="known_result")
                if x.title == "Literature screening report"
            ]
            if not existing_known:
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
        outcomes: list[IterationOutcome] = []

        for iteration in range(completed + 1, int(iterations) + 1):
            self._check_stop()
            self._set_runtime(current_iteration=iteration, current_step="iteration_start")
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
            proposal_raw = self._call(proposer, proposal_prompt, f"iter:{iteration}:proposer")
            proposal = extract_json_object(proposal_raw)
            claim = str(proposal.get("claim") or proposal.get("raw") or "Boş iddia")
            title = str(proposal.get("title") or f"Iteration {iteration} candidate")

            item = self._iteration_item(iteration)
            if item is None:
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
            tool_result = self._tool(tool_request, f"iter:{iteration}:tool")

            verifier_prompt = (
                f"Frozen problem:\n{problem}\n\n"
                f"Candidate {item.id}:\n{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
                "Deterministic tool result:\n"
                + json.dumps(tool_result.as_dict() if tool_result else None, ensure_ascii=False, indent=2)
                + "\n\nBu iddiayı doğrulama mühendisi gibi incele. LLM görüşünü ispat sayma. "
                "Eksik deney, küçük-n testi, SMT kontrolü veya formal proof ihtiyacını belirt. "
                "Tool error ile matematiksel counterexample'ı birbirine karıştırma. SADECE JSON ver: "
                '{"verdict":"PASS|FAIL|INCONCLUSIVE","reason":"...",'
                '"formal_proof_required":true,"counterexample":""}'
            )
            verification = extract_json_object(
                self._call(verifier, verifier_prompt, f"iter:{iteration}:verifier")
            )

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
            critique_raw = self._call(
                critic, critic_prompt, f"iter:{iteration}:critic"
            )

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
            manager_decision = extract_json_object(
                self._call(manager, manager_prompt, f"iter:{iteration}:manager")
            )
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
                already = [
                    x
                    for x in self.state.list_items(kind="counterexample")
                    if x.metadata.get("target_id") == item.id
                ]
                if not already:
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
                already = [
                    x
                    for x in self.state.list_items(kind="counterexample")
                    if x.metadata.get("target_id") == item.id
                ]
                if not already:
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
            self._set_runtime(
                completed_iterations=iteration,
                current_iteration=iteration,
                current_step="iteration_complete",
                next_task=next_task,
                status="RUNNING",
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
                audit = self._call(
                    auditor, audit_prompt, f"iter:{iteration}:checkpoint_audit"
                )
                title_audit = f"Checkpoint audit {iteration}"
                existing_audit = [
                    x for x in self.state.list_items(kind="audit") if x.title == title_audit
                ]
                if not existing_audit:
                    audit_item = self.state.add_item(
                        "audit",
                        title_audit,
                        audit,
                        status="KNOWN",
                        metadata={"iteration": iteration, "independent": True},
                    )
                    checkpoint_path = self.state.checkpoint(
                        f"iteration-{iteration}", note=audit[:1000]
                    )
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
        final_audit = self._call(auditor, final_audit_prompt, "final:audit")
        existing_final = [
            x for x in self.state.list_items(kind="audit") if x.title == "Final independent audit"
        ]
        if existing_final:
            final_item = existing_final[-1]
            checkpoint = self.state.checkpoint("final-resume", note=final_audit[:1500])
        else:
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
        self._set_runtime(
            status="COMPLETED",
            current_step="complete",
            completed_iterations=int(iterations),
            last_error="",
        )

        lines = [
            "# Theorem Research Run",
            "",
            f"Iterations target: {iterations}",
            f"Completed iterations: {self._runtime().get('completed_iterations', 0)}",
            f"Checkpoint: {checkpoint}",
            "",
            "## Outcomes from this invocation",
        ]
        if outcomes:
            for outcome in outcomes:
                lines.append(
                    f"- {outcome.item_id}: [{outcome.status}] {outcome.decision} → {outcome.next_task}"
                )
        else:
            lines.append("- Yeni iterasyon yok; daha önce tamamlanan state yeniden kullanıldı.")
        lines.extend(["", "## Final independent audit", final_audit])
        return "\n".join(lines)
