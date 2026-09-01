from __future__ import annotations

import json
import os
from typing import Any, Callable

from lab.agent import Agent
from lab.code_experiment import (
    CODE_EXPERIMENT_SYSTEM_PROMPT,
    CodeExperimentRunner,
    GuardedExperimentWorkspace,
    WorkspaceActionResult,
)
from lab.code_experiment_settings import load_code_experiment_settings
from lab.partial_resume_theorem_lab import TheoremResearchLab as PartialResumeTheoremResearchLab
from lab.theorem_lab import extract_json_object
from lab.tools import ToolResult


class TheoremCodeExperimentRunner(CodeExperimentRunner):
    """CodeExperimentRunner variant whose trace payload keeps action/result separate."""

    def run(
        self,
        *,
        agent: Agent,
        task: str,
        step_key: str,
        call_agent: Callable[[Agent, str, str], str],
        execute_cached: Callable[[str, dict[str, Any]], WorkspaceActionResult],
    ) -> ToolResult:
        observation = self.workspace.list_files().output
        last_result: WorkspaceActionResult | None = None
        for turn in range(1, self.max_steps + 1):
            prompt = (
                f"EXPERIMENT TASK:\n{task}\n\n"
                f"WORKSPACE / PREVIOUS OBSERVATION:\n{observation[-self.observation_limit:]}\n\n"
                "Choose exactly one next action. Return only the JSON action object. "
                "Use finish only after you have actually run enough code to support the computational conclusion."
            )
            raw = call_agent(agent, prompt, f"{step_key}:plan:{turn}")
            action = extract_json_object(raw)
            action_name = str(action.get("action") or "").lower()
            self.trace.log(
                "code_experiment_action",
                step_key=step_key,
                turn=turn,
                agent=agent.name,
                model=agent.model,
                action=self._action_for_trace(action),
            )
            if action_name == "finish":
                summary = str(action.get("summary") or "Deney tamamlandı.")
                files = self.workspace.list_files()
                payload = {
                    "status": "EXPERIMENT_COMPLETE",
                    "evidence_level": "COMPUTATION_ONLY",
                    "summary": summary,
                    "turns": turn,
                    "workspace": str(self.workspace.root),
                    "files": json.loads(files.output) if files.ok else [],
                    "warning": "Computational evidence is not a proof.",
                }
                self.trace.log("code_experiment_complete", step_key=step_key, **payload)
                return ToolResult(True, "code_experiment", output=summary, metadata=payload)

            last_result = execute_cached(f"{step_key}:action:{turn}", action)
            result_payload = last_result.as_dict()
            self.trace.log(
                "code_experiment_result",
                step_key=step_key,
                turn=turn,
                executed_action=action_name,
                result=result_payload,
            )
            observation = json.dumps(result_payload, ensure_ascii=False, indent=2)

        return ToolResult(
            False,
            "code_experiment",
            output=(last_result.output if last_result else ""),
            error=f"CodeExperimentAgent {self.max_steps} action limitine ulaştı; finish üretmedi.",
            metadata={
                "status": "STEP_LIMIT",
                "evidence_level": "COMPUTATION_ONLY",
                "workspace": str(self.workspace.root),
            },
        )


class TheoremResearchLab(PartialResumeTheoremResearchLab):
    """Partial-resumable theorem lab with an autonomous guarded code-experiment loop."""

    def __init__(self, *args, code_experiment_steps: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        settings = load_code_experiment_settings()
        steps = int(code_experiment_steps or settings.get("max_steps", 8))
        timeout_s = int(settings.get("timeout_s", 60))
        self.code_settings = settings
        self.code_agent: Agent | None = None
        self.code_workspace = GuardedExperimentWorkspace(
            self.state.root / "workspace",
            timeout_s=timeout_s,
        )
        self.code_runner = TheoremCodeExperimentRunner(
            self.code_workspace,
            self.trace,
            max_steps=steps,
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
            configured_model = str(self.code_settings.get("model") or "").strip()
            code_agent = Agent(
                name="CodeExperimentAgent",
                system_prompt=CODE_EXPERIMENT_SYSTEM_PROMPT,
                model=(
                    configured_model
                    or os.environ.get("LAB_CODE_EXPERIMENT_MODEL")
                    or proposer.model
                ),
                temperature=0.2,
                max_tokens=proposer.max_tokens,
            )
        elif not code_agent.system_prompt.strip():
            code_agent.system_prompt = CODE_EXPERIMENT_SYSTEM_PROMPT
        self.code_agent = code_agent

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
