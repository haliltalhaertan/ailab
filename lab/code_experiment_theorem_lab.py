from __future__ import annotations

import os
from typing import Any

from lab.agent import Agent
from lab.code_experiment import (
    CODE_EXPERIMENT_SYSTEM_PROMPT,
    CodeExperimentRunner,
    GuardedExperimentWorkspace,
    WorkspaceActionResult,
)
from lab.partial_resume_theorem_lab import TheoremResearchLab as PartialResumeTheoremResearchLab
from lab.tools import ToolResult


class TheoremResearchLab(PartialResumeTheoremResearchLab):
    """Partial-resumable theorem lab with an autonomous guarded code-experiment loop."""

    def __init__(self, *args, code_experiment_steps: int = 8, **kwargs):
        super().__init__(*args, **kwargs)
        self.code_agent: Agent | None = None
        self.code_workspace = GuardedExperimentWorkspace(self.state.root / "workspace")
        self.code_runner = CodeExperimentRunner(
            self.code_workspace,
            self.trace,
            max_steps=code_experiment_steps,
        )

    def _save_config(
        self,
        problem: str,
        iterations: int,
        literature_query: str | None,
        checkpoint_every: int,
        agents: dict[str, Agent],
    ) -> None:
        augmented = dict(agents)
        if self.code_agent is not None:
            augmented["CodeExperimentAgent"] = self.code_agent
        super()._save_config(problem, iterations, literature_query, checkpoint_every, augmented)

    def run(self, problem: str, *, code_agent: Agent | None = None, proposer: Agent, **kwargs) -> str:
        if code_agent is None:
            code_agent = Agent(
                name="CodeExperimentAgent",
                system_prompt=CODE_EXPERIMENT_SYSTEM_PROMPT,
                model=os.environ.get("LAB_CODE_EXPERIMENT_MODEL") or proposer.model,
                temperature=0.2,
                max_tokens=proposer.max_tokens,
            )
        elif not code_agent.system_prompt.strip():
            code_agent.system_prompt = CODE_EXPERIMENT_SYSTEM_PROMPT
        self.code_agent = code_agent

        # The base workflow has an older enum in the proposal JSON schema. A system-level
        # instruction makes the new experimental tool available without duplicating the
        # large, well-tested theorem loop.
        if "code_experiment" not in proposer.system_prompt:
            proposer.system_prompt = (
                proposer.system_prompt.rstrip()
                + "\n\nTOOL UPDATE: Hesaplamalı deney gerekiyorsa tool_request içinde "
                + "{\"tool\":\"code_experiment\",\"task\":\"deney hedefi\"} kullanabilirsin. "
                + "Bu seçenek, görev mesajındaki eski tool enum listesinde görünmese bile geçerlidir. "
                + "CodeExperimentAgent kod yazıp çalıştıracak; finite computation ispat değildir."
            )
        return super().run(problem, proposer=proposer, **kwargs)

    def _cached_workspace_action(self, cache_key: str, action: dict[str, Any]) -> WorkspaceActionResult:
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            raw = cached.get("result") or {}
            self.trace.log("step_reused", step_key=cache_key, tool="code_experiment_action")
            return WorkspaceActionResult(
                bool(raw.get("ok")),
                str(raw.get("action") or "unknown"),
                str(raw.get("output") or ""),
                str(raw.get("error") or ""),
                dict(raw.get("metadata") or {}),
            )

        result = self.code_workspace.execute(action)
        self._cache_put(
            cache_key,
            {
                "status": "COMPLETE",
                "result": result.as_dict(),
            },
        )
        return result

    def _tool(self, request: dict[str, Any] | None, step_key: str) -> ToolResult | None:
        tool = str((request or {}).get("tool") or "none").strip().lower()
        if tool != "code_experiment":
            return super()._tool(request, step_key)

        cached = self._cache_get(step_key)
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            raw = cached.get("result")
            if isinstance(raw, dict):
                self.trace.log("step_reused", step_key=step_key, tool="code_experiment")
                return ToolResult(
                    bool(raw.get("ok")),
                    "code_experiment",
                    str(raw.get("output") or ""),
                    str(raw.get("error") or ""),
                    dict(raw.get("metadata") or {}),
                )

        self._check_stop()
        self._set_runtime(current_step=step_key)
        if self.code_agent is None:
            result = ToolResult(
                False,
                "code_experiment",
                error="CodeExperimentAgent yapılandırılmamış.",
                metadata={"evidence_level": "COMPUTATION_ONLY"},
            )
        else:
            task = str((request or {}).get("task") or (request or {}).get("goal") or "").strip()
            if not task:
                task = "Aday iddiayı küçük ve deterministik Python deneyleriyle sınamaya çalış."
            self.trace.log(
                "tool_start",
                request={"tool": "code_experiment", "task": task},
                step_key=step_key,
            )
            result = self.code_runner.run(
                agent=self.code_agent,
                task=task,
                step_key=step_key,
                call_agent=self._call,
                execute_cached=self._cached_workspace_action,
            )

        self.trace.log("tool_result", step_key=step_key, **result.as_dict())
        self._cache_put(
            step_key,
            {
                "status": "COMPLETE",
                "result": result.as_dict(),
            },
        )
        return result
