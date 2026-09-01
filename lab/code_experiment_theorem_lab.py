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
from lab.code_experiment_settings import load_code_experiment_settings
from lab.integrity import content_fingerprint
from lab.partial_resume_theorem_lab import TheoremResearchLab as PartialResumeTheoremResearchLab
from lab.tools import ToolResult


class TheoremResearchLab(PartialResumeTheoremResearchLab):
    """Partial-resumable theorem lab with autonomous computational experiments."""

    def __init__(
        self,
        *args,
        code_experiment_steps: int | None = None,
        code_experiment_settings_override: dict[str, Any] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        settings = load_code_experiment_settings()
        if code_experiment_settings_override:
            settings = {**settings, **dict(code_experiment_settings_override)}
        steps = int(code_experiment_steps or settings.get("max_steps", 8))
        timeout_s = int(settings.get("timeout_s", 60))
        memory_limit_mb = int(settings.get("memory_limit_mb", 768))
        max_output_bytes = int(settings.get("max_output_mb", 4)) * 1024 * 1024
        self.code_settings = settings
        self.code_agent: Agent | None = None
        self.code_workspace = GuardedExperimentWorkspace(
            self.state.root / "workspace",
            timeout_s=timeout_s,
            memory_limit_mb=memory_limit_mb,
            max_output_bytes=max_output_bytes,
            cancel_check=lambda: self.stop_path.exists(),
        )
        self.code_runner = CodeExperimentRunner(
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
                model=(configured_model or os.environ.get("LAB_CODE_EXPERIMENT_MODEL") or proposer.model),
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
                + "CodeExperimentAgent kod yazıp gerçekten çalıştıracak. Finite computation ispat değildir; "
                + "finish ancak gerçek başarılı execution evidence sonrası kabul edilir."
            )
        return super().run(problem, proposer=proposer, **kwargs)

    def _cached_workspace_action(self, cache_key: str, action: dict[str, Any]) -> WorkspaceActionResult:
        fingerprint = content_fingerprint("code_experiment_action:v2", action)
        cached = self._cache_get(cache_key)
        if (
            isinstance(cached, dict)
            and cached.get("status") == "COMPLETE"
            and cached.get("fingerprint") == fingerprint
        ):
            raw = cached.get("result") or {}
            self.trace.log("step_reused", step_key=cache_key, tool="code_experiment_action")
            return WorkspaceActionResult(
                bool(raw.get("ok")),
                str(raw.get("action") or "unknown"),
                str(raw.get("output") or ""),
                str(raw.get("error") or ""),
                dict(raw.get("metadata") or {}),
            )
        if isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            self.trace.log("cache_fingerprint_miss", step_key=cache_key, kind="code_experiment_action")

        result = self.code_workspace.execute(action)
        self._cache_put(
            cache_key,
            {"status": "COMPLETE", "fingerprint": fingerprint, "result": result.as_dict()},
        )
        return result

    def _tool(self, request: dict[str, Any] | None, step_key: str) -> ToolResult | None:
        tool = str((request or {}).get("tool") or "none").strip().lower()
        if tool != "code_experiment":
            return super()._tool(request, step_key)

        fingerprint = content_fingerprint(
            "code_experiment_tool:v2",
            {
                "request": request or {},
                "agent_model": getattr(self.code_agent, "model", None),
                "agent_system_prompt": getattr(self.code_agent, "system_prompt", None),
                "reasoning_effort": getattr(self.code_agent, "reasoning_effort", None),
                "max_steps": self.code_runner.max_steps,
                "capabilities": self.code_workspace.capability_summary(),
            },
        )
        cached = self._cache_get(step_key)
        if (
            isinstance(cached, dict)
            and cached.get("status") == "COMPLETE"
            and cached.get("fingerprint") == fingerprint
        ):
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
        elif isinstance(cached, dict) and cached.get("status") == "COMPLETE":
            self.trace.log("cache_fingerprint_miss", step_key=step_key, kind="code_experiment")

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
            {"status": "COMPLETE", "fingerprint": fingerprint, "result": result.as_dict()},
        )
        return result
