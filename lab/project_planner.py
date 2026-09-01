from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from lab.agent import Agent
from lab.client import LLMClient, LLMResponse
from lab.trace import Trace


PROJECT_PLANNER_SYSTEM_PROMPT = """You are ProjectPlanner for an autonomous mathematical/scientific research lab.
Your job is NOT to solve the user's research problem. Convert the user's short research idea into a clean project configuration for the research agents.

Rules:
- Return ONLY one JSON object. No markdown fences and no prose before/after it.
- Preserve the user's actual intent; do not replace it with a different problem.
- Make the initial_problem detailed enough that a ResearchManager can act on it without asking the user basic setup questions.
- Do not claim that a problem is open, novel, proven, impossible, best-known, or state-of-the-art unless that claim was explicitly supplied by the user. Phrase uncertain literature status as something to verify.
- literature_query should be a concise English scholarly search query, not a conclusion.
- Use 'Teorem Araştırması' for proof/theorem/open-math/theoretical-CS style work; otherwise select the closest supported experiment type.
- project_id must be lowercase ASCII kebab-case and reasonably short.
- tags must be a JSON array of short strings.
- description should be one or two compact sentences in the user's language.
- initial_problem should preserve important equations, bounds, constraints, verification requirements, stop rules, and definitions from the user's prompt.

JSON schema:
{
  "title": "human readable project title",
  "project_id": "lowercase-kebab-case",
  "description": "short description",
  "experiment": "Teorem Araştırması | Araştırma Döngüsü | Tartışma | Zincir | Panel",
  "initial_problem": "detailed frozen starting problem for the research team",
  "literature_query": "English scholarly search query",
  "tags": ["tag1", "tag2"]
}
"""

SUPPORTED_EXPERIMENTS = {
    "Teorem Araştırması",
    "Araştırma Döngüsü",
    "Tartışma",
    "Zincir",
    "Panel",
}


@dataclass
class ProjectDraft:
    title: str
    project_id: str
    description: str
    experiment: str
    initial_problem: str
    literature_query: str
    tags: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "project_id": self.project_id,
            "description": self.description,
            "experiment": self.experiment,
            "initial_problem": self.initial_problem,
            "literature_query": self.literature_query,
            "tags": self.tags,
        }


def _slugify(value: str) -> str:
    raw = value.casefold()
    translit = str.maketrans(
        {
            "ç": "c",
            "ğ": "g",
            "ı": "i",
            "ö": "o",
            "ş": "s",
            "ü": "u",
        }
    )
    raw = raw.translate(translit)
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw[:64].strip("-") or "research-project"


def _strip_fence(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _object_slice(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return text
    return text[start : end + 1]


def _repair_string_escapes(text: str) -> str:
    r"""Repair common almost-JSON emitted by LLMs without changing field content.

    The main real-world failure here is raw LaTeX such as ``\Omega`` inside a JSON
    string. JSON only permits a small set of backslash escapes, so ``\O`` is invalid.
    We turn invalid in-string backslashes into literal backslashes and escape raw
    control characters that occasionally appear inside quoted strings.
    """

    out: list[str] = []
    i = 0
    in_string = False
    hex_digits = set("0123456789abcdefABCDEF")
    simple_escapes = set('"\\/bfnrt')

    while i < len(text):
        ch = text[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue

        if ch == '"':
            out.append(ch)
            in_string = False
            i += 1
            continue

        if ch == "\\":
            if i + 1 >= len(text):
                out.append("\\\\")
                i += 1
                continue
            nxt = text[i + 1]
            if nxt in simple_escapes:
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            if nxt == "u" and i + 5 < len(text) and all(c in hex_digits for c in text[i + 2 : i + 6]):
                out.append(text[i : i + 6])
                i += 6
                continue
            out.append("\\\\")
            i += 1
            continue

        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _json_candidates(text: str) -> list[str]:
    raw = _strip_fence(text)
    candidates: list[str] = []
    for candidate in (raw, _object_slice(raw)):
        if not candidate:
            continue
        candidates.append(candidate)
        repaired = _repair_string_escapes(candidate)
        candidates.append(repaired)
        candidates.append(re.sub(r",\s*([}\]])", r"\1", repaired))
    return list(dict.fromkeys(candidates))


def _parse_object(text: str) -> dict[str, Any] | None:
    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


def _repair_prompt(raw: str) -> str:
    return (
        "The previous ProjectPlanner response was intended to be JSON but could not be parsed. "
        "Repair ONLY its formatting. Return exactly one valid JSON object, with no markdown fences or prose. "
        "Preserve the same field meanings and text. Escape all backslashes that appear inside JSON strings correctly, "
        "including LaTeX commands. Required keys: title, project_id, description, experiment, initial_problem, "
        "literature_query, tags.\n\nBROKEN OUTPUT:\n" + raw
    )


def _normalize(data: dict[str, Any], *, user_prompt: str) -> ProjectDraft:
    title = str(data.get("title") or "").strip()
    if not title:
        title = "Research Project"
    project_id = _slugify(str(data.get("project_id") or title))
    description = str(data.get("description") or "").strip()
    experiment = str(data.get("experiment") or "Teorem Araştırması").strip()
    if experiment not in SUPPORTED_EXPERIMENTS:
        experiment = "Teorem Araştırması"
    initial_problem = str(data.get("initial_problem") or "").strip()
    if not initial_problem:
        initial_problem = user_prompt.strip()
    literature_query = str(data.get("literature_query") or "").strip()
    raw_tags = data.get("tags") or []
    if isinstance(raw_tags, str):
        tags = [part.strip() for part in raw_tags.split(",") if part.strip()]
    elif isinstance(raw_tags, list):
        tags = [str(part).strip() for part in raw_tags if str(part).strip()]
    else:
        tags = []
    return ProjectDraft(
        title=title,
        project_id=project_id,
        description=description,
        experiment=experiment,
        initial_problem=initial_problem,
        literature_query=literature_query,
        tags=tags[:12],
    )


def plan_project(
    user_prompt: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    trace: Trace | None = None,
    client: LLMClient | None = None,
) -> ProjectDraft:
    prompt = (user_prompt or "").strip()
    if not prompt:
        raise ValueError("Proje promptu boş olamaz.")

    model_id = (model or os.environ.get("LAB_PROJECT_PLANNER_MODEL") or "openai/gpt-4o-mini").strip()
    agent = Agent(
        name="ProjectPlanner",
        system_prompt=PROJECT_PLANNER_SYSTEM_PROMPT,
        model=model_id,
        temperature=0.2,
        max_tokens=4000,
        reasoning_effort=reasoning_effort,
        client=client,
    )
    messages = [{"role": "user", "content": prompt}]
    content, response = agent.respond(messages)
    if trace is not None:
        trace.agent_call(agent.name, response.model, agent.temperature, messages, response)
        trace.log("project_planner_output", model=response.model, reasoning_effort=agent.reasoning_effort, raw_output=content)
    data = _parse_object(content)
    if data is None:
        repair_messages = [{"role": "user", "content": _repair_prompt(content)}]
        repaired_content, repaired_response = agent.respond(repair_messages)
        if trace is not None:
            trace.log("project_planner_json_repair", original_output=content, repaired_output=repaired_content)
            trace.agent_call(agent.name, repaired_response.model, agent.temperature, repair_messages, repaired_response)
        data = _parse_object(repaired_content)
        if data is None:
            raise ValueError("ProjectPlanner JSON çıktısı iki aşamalı onarımdan sonra da ayrıştırılamadı.")
    return _normalize(data, user_prompt=prompt)
