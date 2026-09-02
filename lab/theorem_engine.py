from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from lab.agent import Agent
from lab.code_experiment import CODE_EXPERIMENT_SYSTEM_PROMPT, CodeExperimentRunner, GuardedExperimentWorkspace, WorkspaceActionResult
from lab.code_experiment_settings import load_code_experiment_settings, load_code_experiment_settings_from_dict
from lab.integrity import content_fingerprint
from lab.json_io import StructuredOutputError, parse_json_object, repair_instruction
from lab.literature import LiteratureClient, LiteratureSearchEmpty, Paper
from lab.prompts import checkpoint_prompt, critic_prompt, literature_prompt, manager_prompt, proposal_prompt, verifier_prompt
from lab.research_state import ResearchState
from lab.run_controller import ResearchPaused, ResearchStopped, RunController, atomic_json, now_iso, retryable
from lab.status_guard import choose_status
from lab.step_store import StepStore
from lab.tool_registry import ToolRegistry
from lab.tools import ResearchToolbox, ToolResult
from lab.trace import Trace


@dataclass
class IterationOutcome:
    item_id: str
    decision: str
    status: str
    next_task: str


def paper_context(papers: list[Paper]) -> str:
    if not papers:
        return "(retrieval returned no usable candidate records; novelty is INCONCLUSIVE)"
    rows = []
    for i, paper in enumerate(papers, 1):
        authors = ", ".join(paper.authors[:3])
        rows.append(f"{i}. {paper.title} ({paper.year or '?'}) — {authors} — {paper.url}")
    return "\n".join(rows)


class TheoremResearchLab:
    """Single production theorem workflow.

    Responsibilities are composed rather than inherited:
    - StepStore: SQLite cache, partial responses, frozen iteration snapshots
    - RunController: lock, runtime cursor, stop and retry policy
    - ToolRegistry: one source of truth for tool schema/dispatch
    - ResearchState: human-readable scientific ledger

    LLMs can propose statuses, but code-side evidence guards decide what is
    actually written to the ledger.
    """

    PARTIAL_RESUME_CHAR_LIMIT = 60_000
    PARTIAL_FLUSH_INTERVAL_S = 0.25
    PARTIAL_FLUSH_CHARS = 1024

    def __init__(
        self,
        trace: Trace,
        state: ResearchState,
        literature: LiteratureClient | None = None,
        toolbox: ResearchToolbox | None = None,
        *,
        max_retries: int = 3,
        code_experiment_steps: int | None = None,
        code_experiment_settings_override: dict[str, Any] | None = None,
    ):
        self.trace = trace
        self.state = state
        self.literature = literature or LiteratureClient()
        self.step_store = StepStore(state.root)
        self.controller = RunController(state.root, trace, max_retries=max_retries)
        self.toolbox = toolbox or ResearchToolbox()
        self.registry = ToolRegistry(self.toolbox)
        base_settings = load_code_experiment_settings()
        if code_experiment_settings_override:
            base_settings = load_code_experiment_settings_from_dict({**base_settings, **code_experiment_settings_override})
        self.code_settings = base_settings
        steps = int(code_experiment_steps or base_settings.get("max_steps", 8))
        self.code_agent: Agent | None = None
        self.code_workspace = GuardedExperimentWorkspace(
            self.state.root / "workspace",
            timeout_s=int(base_settings.get("timeout_s", 60)),
            memory_limit_mb=int(base_settings.get("memory_limit_mb", 768)),
            max_output_bytes=int(base_settings.get("max_output_mb", 4)) * 1024 * 1024,
            pid_limit=int(base_settings.get("pid_limit", 8)),
            cpu_limit=float(base_settings.get("cpu_limit", 1.0)),
            cancel_check=lambda: self.controller.stop_path.exists(),
            container_engine=str(base_settings.get("container_engine") or "") or None,
            container_image=str(base_settings.get("container_image") or "python:3.12-slim"),
        )
        self.code_runner = CodeExperimentRunner(self.code_workspace, self.trace, max_steps=steps)
        self.registry.register("code_experiment", self._code_experiment_placeholder)
        self._literature_reliable = False

    # Compatibility helpers used by existing tests/UI.
    @property
    def runtime_path(self):
        return self.controller.runtime_path

    @property
    def config_path(self):
        return self.controller.config_path

    @property
    def stop_path(self):
        return self.controller.stop_path

    def _runtime(self) -> dict[str, Any]:
        return self.controller.runtime()

    def _set_runtime(self, **updates: Any) -> dict[str, Any]:
        return self.controller.set_runtime(**updates)

    def _check_stop(self) -> None:
        self.controller.check_stop()

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        return self.step_store.get_step(key)

    def _cache_put(self, key: str, value: dict[str, Any]) -> None:
        self.step_store.put_step(key, value)

    def _cache_delete(self, key: str) -> None:
        self.step_store.delete_step(key)

    def _partial_get(self, key: str) -> dict[str, Any] | None:
        return self.step_store.get_partial(key)

    def _partial_put(self, key: str, value: dict[str, Any]) -> None:
        self.step_store.put_partial(key, value)

    def _partial_clear(self, key: str) -> None:
        self.step_store.clear_partial(key)

    @staticmethod
    def _code_experiment_placeholder(request: dict[str, Any]) -> ToolResult:
        return ToolResult(False, "code_experiment", error="code_experiment requires theorem-run context")

    def _save_config(
        self,
        problem: str,
        iterations: int,
        literature_query: str | None,
        checkpoint_every: int,
        agents: dict[str, Agent],
    ) -> None:
        if self.code_agent is not None:
            agents = {**agents, "CodeExperimentAgent": self.code_agent}
        payload = {
            "config_version": 3,
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
                    "reasoning_effort": agent.reasoning_effort,
                }
                for role, agent in agents.items()
            },
            "code_experiment": dict(self.code_settings),
            "saved_at": now_iso(),
        }
        atomic_json(self.config_path, payload)

    def _llm_fingerprint(self, agent: Agent, prompt: str) -> str:
        # Model is deliberately excluded. A model override on resume applies only
        # to incomplete work; already-completed steps remain immutable evidence.
        return content_fingerprint(
            "llm_step:v3",
            {
                "agent": agent.name,
                "system_prompt": agent.system_prompt,
                "temperature": agent.temperature,
                "max_tokens": agent.max_tokens,
                "reasoning_effort": agent.reasoning_effort,
                "prompt": prompt,
            },
        )

    @staticmethod
    def _tail(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return "[... earlier visible work truncated ...]\n" + text[-limit:]

    def _resume_messages(self, prompt: str, partial: dict[str, Any] | None, *, current_model: str | None) -> list[dict[str, Any]]:
        if not partial:
            return [{"role": "user", "content": prompt}]
        reasoning = str(partial.get("reasoning") or "")
        content = str(partial.get("content") or "")
        details = partial.get("reasoning_details")
        same_model = str(partial.get("model") or "") == str(current_model or "")
        if details and same_model:
            assistant: dict[str, Any] = {"role": "assistant", "content": content}
            if reasoning:
                assistant["reasoning"] = reasoning
            assistant["reasoning_details"] = details
            return [
                {"role": "user", "content": prompt},
                assistant,
                {
                    "role": "user",
                    "content": "The previous response was interrupted. Continue from the provider-visible structured reasoning above, verify it, and return a COMPLETE final response in the original requested format.",
                },
            ]
        limit = max(2_000, int(self.PARTIAL_RESUME_CHAR_LIMIT))
        context = (
            "\n\n--- INTERRUPTED STEP: SOFT RESUME CONTEXT ---\n"
            f"Previous model: {partial.get('model', '')}\n"
            "Structured provider reasoning is replayed only when the model is unchanged. "
            "Reuse the visible text below, check it, and return a COMPLETE final response.\n\n"
            f"PARTIAL REASONING:\n{self._tail(reasoning, limit // 2) or '(none)'}\n\n"
            f"PARTIAL CONTENT:\n{self._tail(content, limit // 2) or '(none)'}\n"
            "--- END SOFT RESUME CONTEXT ---"
        )
        return [{"role": "user", "content": prompt + context}]

    def _persist_partial(
        self,
        *,
        agent: Agent,
        step_key: str,
        prompt: str,
        fingerprint: str,
        buffers: dict[str, Any],
        attempt: int,
        force: bool = False,
    ) -> None:
        now_mono = time.monotonic()
        dirty = int(buffers.get("dirty_chars", 0) or 0)
        last_flush = float(buffers.get("last_flush", 0.0) or 0.0)
        if not force and dirty < self.PARTIAL_FLUSH_CHARS and now_mono - last_flush < self.PARTIAL_FLUSH_INTERVAL_S:
            return
        if not buffers.get("reasoning") and not buffers.get("content") and not buffers.get("reasoning_details"):
            return
        payload = {
            "status": "PARTIAL",
            "fingerprint": fingerprint,
            "agent": agent.name,
            "model": agent.model,
            "reasoning_effort": agent.reasoning_effort,
            "original_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "reasoning": str(buffers.get("reasoning") or ""),
            "reasoning_details": buffers.get("reasoning_details") or None,
            "content": str(buffers.get("content") or ""),
            "attempt": int(attempt),
            "updated_at": now_iso(),
        }
        self._partial_put(step_key, payload)
        buffers["last_flush"] = now_mono
        buffers["dirty_chars"] = 0

    def _stream_callback(self, agent: Agent, step_key: str, prompt: str, fingerprint: str, buffers: dict[str, Any], attempt: int):
        def callback(channel: str, delta: Any) -> None:
            if isinstance(delta, str) and channel in {"reasoning", "content"}:
                buffers[channel] = str(buffers.get(channel) or "") + delta
                buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + len(delta)
            elif channel == "reasoning_details" and delta:
                details = buffers.setdefault("reasoning_details", [])
                details.extend(delta if isinstance(delta, list) else [delta])
                try:
                    buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + len(json.dumps(delta, ensure_ascii=False))
                except Exception:
                    buffers["dirty_chars"] = int(buffers.get("dirty_chars", 0) or 0) + 1
            if channel in {"reasoning", "content", "reasoning_details"}:
                self._persist_partial(agent=agent, step_key=step_key, prompt=prompt, fingerprint=fingerprint, buffers=buffers, attempt=attempt)
            self.trace.log("agent_stream", agent=agent.name, model=agent.model, reasoning_effort=agent.reasoning_effort, step_key=step_key, channel=channel, delta=delta)
            try:
                self._check_stop()
            except ResearchStopped:
                self._persist_partial(agent=agent, step_key=step_key, prompt=prompt, fingerprint=fingerprint, buffers=buffers, attempt=attempt, force=True)
                raise
        return callback

    def _call(self, agent: Agent, prompt: str, step_key: str) -> str:
        fingerprint = self._llm_fingerprint(agent, prompt)
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            if cached.get("fingerprint") == fingerprint:
                self._partial_clear(step_key)
                if cached.get("model") and cached.get("model") != agent.model:
                    self.trace.log("model_override_reused_completed_step", step_key=step_key, cached_model=cached.get("model"), current_model=agent.model)
                self.trace.log("step_reused", step_key=step_key, agent=agent.name, model=cached.get("model"), fingerprint=fingerprint)
                return str(cached.get("content", ""))
            self.trace.log("cache_fingerprint_miss", step_key=step_key, kind="llm")
            self._cache_delete(step_key)

        partial = self._partial_get(step_key)
        if partial and partial.get("fingerprint") != fingerprint:
            self.trace.log("partial_fingerprint_miss", step_key=step_key)
            self._partial_clear(step_key)
            partial = None

        self._check_stop()
        self._set_runtime(current_step=step_key)
        for attempt in range(1, self.controller.max_retries + 1):
            partial = self._partial_get(step_key)
            messages = self._resume_messages(prompt, partial, current_model=agent.model)
            buffers: dict[str, Any] = {
                "reasoning": str((partial or {}).get("reasoning") or ""),
                "reasoning_details": list((partial or {}).get("reasoning_details") or []),
                "content": str((partial or {}).get("content") or ""),
                "dirty_chars": 0,
                "last_flush": time.monotonic(),
            }
            if partial:
                self.trace.log(
                    "partial_resume_loaded",
                    step_key=step_key,
                    agent=agent.name,
                    previous_model=partial.get("model"),
                    current_model=agent.model,
                    structured=bool(partial.get("reasoning_details") and partial.get("model") == agent.model),
                    reasoning_chars=len(str(partial.get("reasoning") or "")),
                    content_chars=len(str(partial.get("content") or "")),
                )
            self.trace.log(
                "agent_start",
                agent=agent.name,
                model=agent.model,
                temperature=agent.temperature,
                reasoning_effort=agent.reasoning_effort,
                system_prompt=agent.system_prompt,
                prompt=prompt,
                step_key=step_key,
                attempt=attempt,
                soft_resume=bool(partial),
                fingerprint=fingerprint,
            )
            try:
                content, response = agent.respond(messages, stream_callback=self._stream_callback(agent, step_key, prompt, fingerprint, buffers, attempt))
                self.trace.agent_call(agent.name, response.model, agent.temperature, messages, response)
                self._cache_put(
                    step_key,
                    {
                        "status": "COMPLETE",
                        "fingerprint": fingerprint,
                        "content": content,
                        "model": response.model,
                        "reasoning_effort": agent.reasoning_effort,
                        "completed_at": now_iso(),
                        "soft_resumed": bool(partial),
                    },
                )
                self._partial_clear(step_key)
                return content
            except ResearchStopped:
                self._persist_partial(agent=agent, step_key=step_key, prompt=prompt, fingerprint=fingerprint, buffers=buffers, attempt=attempt, force=True)
                raise
            except Exception as exc:
                self._persist_partial(agent=agent, step_key=step_key, prompt=prompt, fingerprint=fingerprint, buffers=buffers, attempt=attempt, force=True)
                do_retry = retryable(exc) and attempt < self.controller.max_retries
                self.trace.log("agent_error", agent=agent.name, model=agent.model, step_key=step_key, attempt=attempt, retrying=do_retry, error=repr(exc))
                if not do_retry:
                    raise ResearchPaused(f"{agent.name} / {agent.model} adımında hata: {exc}") from exc
                wait_s = min(2 ** (attempt - 1), 8)
                self.trace.log("agent_retry", agent=agent.name, step_key=step_key, next_attempt=attempt + 1, wait_s=wait_s, soft_resume=True)
                time.sleep(wait_s)
                self._check_stop()
        raise ResearchPaused(f"{step_key} tamamlanamadı")

    def _call_json(self, agent: Agent, prompt: str, step_key: str) -> dict[str, Any]:
        raw = self._call(agent, prompt, step_key)
        try:
            return parse_json_object(raw)
        except StructuredOutputError as first:
            self.trace.log("structured_output_parse_failed", step_key=step_key, agent=agent.name, error=str(first))
        repair_raw = self._call(agent, repair_instruction(raw), f"{step_key}:json_repair")
        try:
            repaired = parse_json_object(repair_raw)
        except StructuredOutputError as exc:
            self.trace.log("structured_output_repair_failed", step_key=step_key, agent=agent.name, error=str(exc))
            raise ResearchPaused(f"{agent.name} geçerli JSON üretemedi; araştırma fail-closed olarak durduruldu: {exc}") from exc
        self.trace.log("structured_output_repaired", step_key=step_key, agent=agent.name)
        return repaired

    def _search_literature(self, query: str, limit: int = 8) -> list[Paper]:
        fingerprint = content_fingerprint("literature_search:v3", {"query": query.strip(), "limit": int(limit), "client": type(self.literature).__name__})
        key = f"literature:search:{fingerprint[:20]}"
        cached = self._cache_get(key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            papers = []
            for raw in cached.get("papers", []):
                try:
                    papers.append(Paper(**raw))
                except TypeError:
                    continue
            self._literature_reliable = bool(papers)
            self.trace.log("step_reused", step_key=key, records=len(papers), fingerprint=fingerprint)
            return papers
        self._check_stop()
        self.trace.log("literature_search_start", query=query, limit=limit, fingerprint=fingerprint)
        try:
            papers = self.literature.search(query, limit=limit)
        except LiteratureSearchEmpty as exc:
            self._literature_reliable = False
            self.trace.log("literature_search_inconclusive", query=query, warning=str(exc), fingerprint=fingerprint)
            return []
        except Exception as exc:
            self._literature_reliable = False
            self.trace.log("literature_search_error", query=query, error=str(exc), fingerprint=fingerprint)
            return []
        payload = [paper.as_dict() for paper in papers]
        self._literature_reliable = bool(payload)
        self.trace.log("literature_search", query=query, results=payload, fingerprint=fingerprint)
        self._cache_put(key, {"status": "COMPLETE", "fingerprint": fingerprint, "query": query, "limit": int(limit), "papers": payload, "completed_at": now_iso()})
        return papers

    def _cached_workspace_action(self, cache_key: str, action: dict[str, Any]) -> WorkspaceActionResult:
        fingerprint = content_fingerprint("code_experiment_action:v3", action)
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            raw = cached.get("result") or {}
            self.trace.log("step_reused", step_key=cache_key, tool="code_experiment_action")
            return WorkspaceActionResult(bool(raw.get("ok")), str(raw.get("action") or "unknown"), str(raw.get("output") or ""), str(raw.get("error") or ""), dict(raw.get("metadata") or {}))
        result = self.code_workspace.execute(action)
        self._cache_put(cache_key, {"status": "COMPLETE", "fingerprint": fingerprint, "result": result.as_dict(), "completed_at": now_iso()})
        return result

    def _run_code_experiment(self, request: dict[str, Any], step_key: str) -> ToolResult:
        if self.code_agent is None:
            return ToolResult(False, "code_experiment", error="CodeExperimentAgent yapılandırılmamış.", metadata={"evidence_level": "COMPUTATION_ONLY"})
        task = str(request.get("task") or request.get("goal") or "").strip() or "Aday iddiayı küçük deterministic deneylerle sınamaya çalış."
        return self.code_runner.run(agent=self.code_agent, task=task, step_key=step_key, call_agent=self._call, execute_cached=self._cached_workspace_action)

    def _tool(self, request: dict[str, Any] | None, step_key: str) -> ToolResult | None:
        fingerprint = content_fingerprint("tool_step:v3", request or {"tool": "none"})
        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE" and cached.get("fingerprint") == fingerprint:
            raw = cached.get("result")
            if isinstance(raw, dict):
                self.trace.log("step_reused", step_key=step_key, tool=raw.get("tool"))
                return ToolResult(bool(raw.get("ok")), str(raw.get("tool") or "unknown"), str(raw.get("output") or ""), str(raw.get("error") or ""), dict(raw.get("metadata") or {}))
            return None
        self._check_stop()
        self._set_runtime(current_step=step_key)
        name = str((request or {}).get("tool") or "none").lower()
        if name not in {"", "none"}:
            self.trace.log("tool_start", request=request, step_key=step_key)
        result = self._run_code_experiment(request or {}, step_key) if name == "code_experiment" else self.registry.execute(request)
        if result:
            self.trace.log("tool_result", step_key=step_key, **result.as_dict())
        self._cache_put(step_key, {"status": "COMPLETE", "fingerprint": fingerprint, "result": result.as_dict() if result else None, "completed_at": now_iso()})
        return result

    def _iteration_item(self, iteration: int):
        candidates = [item for item in self.state.list_items(kind="conjecture") if int(item.metadata.get("iteration", -1)) == iteration]
        return candidates[-1] if candidates else None

    def _iteration_snapshot(self, iteration: int, next_task: str) -> dict[str, Any]:
        existing = self.step_store.get_iteration_snapshot(iteration)
        if existing:
            return existing
        context = self.state.research_context()
        revision = self.state.revision()
        self.step_store.put_iteration_snapshot(iteration, ledger_revision=revision, ledger_context=context, payload={"next_task": next_task})
        self.trace.log("iteration_snapshot_frozen", iteration=iteration, ledger_revision=revision, ledger_context_chars=len(context))
        return self.step_store.get_iteration_snapshot(iteration) or {}

    def _ensure_item_matches_proposal(self, iteration: int, proposal: dict[str, Any], snapshot: dict[str, Any]):
        claim = str(proposal.get("claim") or "Boş iddia")
        title = str(proposal.get("title") or f"Iteration {iteration} candidate")
        proposal_hash = content_fingerprint("proposal:v1", proposal)
        item = self._iteration_item(iteration)
        if item is None:
            item = self.state.add_item(
                "conjecture",
                title=title,
                claim=claim,
                metadata={
                    "iteration": iteration,
                    "proposal": proposal,
                    "proposal_hash": proposal_hash,
                    "ledger_revision": snapshot.get("ledger_revision"),
                },
            )
            self.trace.log("state_change", action="create", item_id=item.id, kind="conjecture", old_status=None, new_status="OPEN", title=title, claim=claim)
        else:
            old_hash = str(item.metadata.get("proposal_hash") or "")
            if old_hash and old_hash != proposal_hash:
                raise ResearchPaused(
                    f"Iteration {iteration} integrity mismatch: ledger item {item.id} was created from a different proposal. Evidence will not be attached to a mismatched claim."
                )
            if not old_hash and item.claim.strip() != claim.strip():
                raise ResearchPaused(
                    f"Iteration {iteration} legacy integrity mismatch: cached proposal claim differs from ledger item {item.id}."
                )
        self.step_store.update_iteration_payload(iteration, proposal=proposal, proposal_hash=proposal_hash, item_id=item.id)
        return item

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
        code_agent: Agent | None = None,
        iterations: int = 5,
        literature_query: str | None = None,
        checkpoint_every: int = 2,
    ) -> str:
        if code_agent is None:
            code_agent = Agent(
                name="CodeExperimentAgent",
                system_prompt=CODE_EXPERIMENT_SYSTEM_PROMPT,
                model=str(self.code_settings.get("model") or os.environ.get("LAB_CODE_EXPERIMENT_MODEL") or proposer.model),
                temperature=0.2,
                max_tokens=proposer.max_tokens,
            )
        elif not code_agent.system_prompt.strip():
            code_agent.system_prompt = CODE_EXPERIMENT_SYSTEM_PROMPT
        self.code_agent = code_agent
        agents = {
            "ResearchManager": manager,
            "Theorist": proposer,
            "AdversarialCritic": critic,
            "VerificationEngineer": verifier,
            "IndependentAuditor": auditor,
        }
        if literature_agent is not None:
            agents["LiteratureScout"] = literature_agent
        agents["CodeExperimentAgent"] = code_agent
        self._save_config(problem, iterations, literature_query, checkpoint_every, agents)
        self.controller.clear_stale_stop()
        try:
            with self.controller.lock:
                self.trace.log("project_lock_acquired", project_root=str(self.state.root), pid=os.getpid())
                try:
                    result = self._run_inner(
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
                    self._set_runtime(status="COMPLETED", last_error="")
                    return result
                finally:
                    self.trace.log("project_lock_releasing", project_root=str(self.state.root))
        except ResearchStopped as exc:
            self._set_runtime(status="STOPPED", last_error=str(exc))
            self.trace.log("run_stopped", error=str(exc))
            return "# Araştırma durduruldu\n\nKalıcı state, iteration snapshot ve tamamlanan adımlar korundu. Devam edildiğinde ilk tamamlanmamış adımdan ilerlenir."
        except ResearchPaused as exc:
            self._set_runtime(status="PAUSED_ERROR", last_error=str(exc))
            self.trace.log("run_paused", error=str(exc))
            return f"# Araştırma hata nedeniyle beklemeye alındı\n\n{exc}\n\nBelirsiz/bozuk structured output veya integrity uyuşmazlığı sessizce geçilmedi."

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
        next_task = str(runtime.get("next_task") or "").strip() or "Problemi daralt; bilinen sınırları ihlal etmeyen, çürütülebilir tek bir lemma, construction veya lower-bound mekanizması öner."
        self._set_runtime(status="RUNNING", last_error="")

        papers = self._search_literature(literature_query or problem)
        literature_context = paper_context(papers)
        if literature_agent is not None:
            lprompt = literature_prompt(problem, literature_context, reliable=self._literature_reliable)
            literature_report = self._call(literature_agent, lprompt, "literature:agent")
            title = "Literature screening report"
            existing = [x for x in self.state.list_items(kind="known_result") if x.title == title]
            if not existing:
                known = self.state.add_item(
                    "known_result",
                    title,
                    literature_report,
                    status="KNOWN",
                    metadata={"screening_only": True, "retrieval_reliable": self._literature_reliable},
                )
                self.trace.log("state_change", action="create", item_id=known.id, kind="known_result", status="KNOWN", title=title)
            reliability_label = "retrieval had candidate records" if self._literature_reliable else "retrieval inconclusive - NOT a novelty signal"
            literature_context += f"\n\nLLM LITERATURE SCREEN ({reliability_label}):\n{literature_report}"

        outcomes: list[IterationOutcome] = []
        for iteration in range(completed + 1, int(iterations) + 1):
            self._check_stop()
            self._set_runtime(current_iteration=iteration, current_step="iteration_start")
            snapshot = self._iteration_snapshot(iteration, next_task)
            frozen_next_task = str(snapshot.get("next_task") or next_task)
            ledger_context = str(snapshot.get("ledger_context") or "")
            self.trace.log("iteration_start", iteration=iteration, next_task=frozen_next_task, ledger_revision=snapshot.get("ledger_revision"))

            proposal = self._call_json(
                proposer,
                proposal_prompt(problem, literature_context, ledger_context, frozen_next_task, self.registry),
                f"iter:{iteration}:proposer",
            )
            item = self._ensure_item_matches_proposal(iteration, proposal, snapshot)
            claim = item.claim

            request = proposal.get("tool_request")
            tool_request = request if isinstance(request, dict) else None
            tool_result = self._tool(tool_request, f"iter:{iteration}:tool")

            verification = self._call_json(
                verifier,
                verifier_prompt(problem, item.id, proposal, tool_result.as_dict() if tool_result else None),
                f"iter:{iteration}:verifier",
            )
            critique = self._call_json(
                critic,
                critic_prompt(problem, item.id, claim, proposal, tool_result.as_dict() if tool_result else None, verification, ledger_context),
                f"iter:{iteration}:critic",
            )
            manager_decision = self._call_json(
                manager,
                manager_prompt(problem, item.id, claim, tool_result.as_dict() if tool_result else None, verification, critique),
                f"iter:{iteration}:manager",
            )
            decision = str(manager_decision.get("decision") or "REVISE").upper()
            requested_status = str(manager_decision.get("status") or "OPEN").upper()
            # Formal verification is machine-triggered; manager omission cannot hide it.
            if tool_result and tool_result.tool == "lean" and tool_result.ok and (tool_result.metadata or {}).get("formal_verified"):
                requested_status = "PROVEN"
            guard = choose_status(
                requested_status,
                tool_result=tool_result,
                verifier=verification,
                critic=critique,
                expected_item_id=item.id,
                expected_iteration=iteration,
                expected_claim_hash=content_fingerprint("claim:v1", claim),
            )
            status = guard.granted
            if guard.downgraded:
                self.trace.log(
                    "status_downgraded_by_guard",
                    iteration=iteration,
                    item_id=item.id,
                    requested=requested_status,
                    granted=status,
                    reason=guard.reason,
                    guard=guard.metadata,
                )

            counterexample = str(verification.get("counterexample") or critique.get("counterexample") or "").strip()
            tool_counterexample = bool(tool_result and tool_result.tool == "tropical_grid" and str((tool_result.metadata or {}).get("status") or "").upper() == "COUNTEREXAMPLE")
            if status == "FAIL" and (tool_counterexample or counterexample):
                existing = [x for x in self.state.list_items(kind="counterexample") if x.metadata.get("target_id") == item.id]
                if not existing:
                    desc = "Deterministic tropical grid counterexample" if tool_counterexample else counterexample
                    counter = self.state.add_counterexample(item.id, desc, payload=(tool_result.metadata if tool_counterexample and tool_result else None))
                    self.trace.log("state_change", action="counterexample", item_id=counter.id, target_id=item.id, kind="counterexample", status="KNOWN", detail=desc)
            else:
                evidence = [
                    "Verifier: " + json.dumps(verification, ensure_ascii=False),
                    "Critic: " + json.dumps(critique, ensure_ascii=False),
                    "Manager: " + json.dumps(manager_decision, ensure_ascii=False),
                    "StatusGuard: " + guard.reason,
                ]
                if tool_result:
                    evidence.append("Tool: " + json.dumps(tool_result.as_dict(), ensure_ascii=False))
                metadata: dict[str, Any] = {"status_guard": guard.metadata, "proposal_hash": item.metadata.get("proposal_hash")}
                if status == "PROVEN" and tool_result:
                    formal_metadata = dict(tool_result.metadata or {})
                    if formal_metadata.get("file") and not formal_metadata.get("lean_file"):
                        formal_metadata["lean_file"] = formal_metadata["file"]
                    metadata.update(formal_metadata)
                self.state.update_item(item.id, status=status, evidence=evidence, metadata=metadata)

            old_status = item.status
            self.trace.log("state_change", action="status", item_id=item.id, kind="conjecture", old_status=old_status, new_status=status, decision=decision, reason=str(manager_decision.get("reason") or ""))
            next_task = str(manager_decision.get("next_task") or frozen_next_task)
            outcomes.append(IterationOutcome(item.id, decision, status, next_task))
            self.trace.log("iteration_end", iteration=iteration, item_id=item.id, decision=decision, status=status, next_task=next_task)
            self._set_runtime(completed_iterations=iteration, current_iteration=iteration, current_step="iteration_complete", next_task=next_task, status="RUNNING")

            if checkpoint_every and iteration % checkpoint_every == 0:
                ledger = self.state.research_context(recent_limit=50)
                audit = self._call(auditor, checkpoint_prompt(problem, ledger, iteration), f"iter:{iteration}:checkpoint_audit")
                title = f"Checkpoint audit {iteration}"
                if not [x for x in self.state.list_items(kind="audit") if x.title == title]:
                    audit_item = self.state.add_item("audit", title, audit, status="KNOWN", metadata={"iteration": iteration, "independent": True})
                    checkpoint_path = self.state.checkpoint(f"iteration-{iteration}", note=audit[:1000])
                    self.trace.log("checkpoint", iteration=iteration, audit_item_id=audit_item.id, path=str(checkpoint_path), audit=audit)

        final_ledger = self.state.research_context(recent_limit=80)
        final_audit = self._call(auditor, checkpoint_prompt(problem, final_ledger, int(iterations), final=True), "final:audit")
        final_path = self.state.checkpoint("final", note=final_audit[:1500])
        self.trace.log("checkpoint", final=True, path=str(final_path), audit=final_audit)
        lines = ["# Teorem Araştırması Sonucu", "", "## Tur Sonuçları"]
        for outcome in outcomes:
            lines.append(f"- `{outcome.item_id}` — **{outcome.status}** — {outcome.decision} — next: {outcome.next_task}")
        if not outcomes:
            lines.append("- Yeni tur çalıştırılmadı; mevcut state zaten istenen iterasyona kadar tamamlanmıştı.")
        lines += ["", "## Final Bağımsız Audit", final_audit, "", f"Checkpoint: `{final_path}`"]
        return "\n".join(lines)
