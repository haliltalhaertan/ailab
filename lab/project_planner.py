from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from lab.agent import Agent
from lab.client import LLMResponse


EXPERIMENTS = (
    "Teorem Araştırması",
    "Araştırma Döngüsü",
    "Tartışma",
    "Zincir",
    "Panel",
)

PROJECT_PLANNER_SYSTEM_PROMPT = """Sen AI Lab için ProjectPlanner ajanısın.
Kullanıcının tek bir doğal dil promptundan düzenlenebilir bir araştırma projesi taslağı üret.

Amaç:
- Kullanıcının gerçek niyetini koru; istemediği yeni hedefler uydurma.
- Problem araştırma için yeterince açık, sınırları belli ve yürütülebilir olsun.
- Kullanıcı matematik/teorik CS açık problemi araştırmak istiyorsa Teorem Araştırması seç.
- Hızlı fikir-eleştiri keşfi ise Araştırma Döngüsü; iki karşıt görüş ise Tartışma;
  tek yönlü iş akışı ise Zincir; bağımsız çoklu görüş ise Panel seç.
- Bir problemin literatürde açık olduğunu doğrulamadan 'çözülmemiştir' diye ilan etme.
  Gerekirse problem metnine 'literatür taramasıyla doğrulanacak' yaz.
- literature_query kısa, arama motoruna uygun İngilizce sorgu olsun.
- tags 3-8 kısa etiketten oluşsun.
- project_id yalnızca küçük ASCII harf, rakam ve tire içersin.

SADECE geçerli JSON döndür. Markdown/code fence/açıklama kullanma.
Şema:
{
  "title": "kısa proje adı",
  "project_id": "ascii-slug",
  "description": "1-3 cümle kısa açıklama",
  "experiment": "Teorem Araştırması | Araştırma Döngüsü | Tartışma | Zincir | Panel",
  "problem": "ajanlara verilecek ayrıntılı başlangıç problemi",
  "literature_query": "English search query",
  "tags": ["tag1", "tag2"]
}
"""


@dataclass(frozen=True)
class ProjectDraft:
    title: str
    project_id: str
    description: str
    experiment: str
    problem: str
    literature_query: str
    tags: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("ı", "i").replace("ğ", "g").replace("ü", "u")
    value = value.replace("ş", "s").replace("ö", "o").replace("ç", "c")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:60] or "research-project"


def _extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("ProjectPlanner geçerli JSON döndürmedi.")
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("ProjectPlanner JSON çıktısı ayrıştırılamadı.") from exc
    if not isinstance(value, dict):
        raise ValueError("ProjectPlanner çıktısı JSON object olmalı.")
    return value


def parse_project_draft(text: str, user_prompt: str) -> ProjectDraft:
    data = _extract_json(text)
    title = str(data.get("title") or "Yeni Araştırma Projesi").strip()
    project_id = _slug(str(data.get("project_id") or title))
    description = str(data.get("description") or user_prompt).strip()
    experiment = str(data.get("experiment") or "Teorem Araştırması").strip()
    if experiment not in EXPERIMENTS:
        experiment = "Teorem Araştırması"
    problem = str(data.get("problem") or user_prompt).strip()
    if not problem:
        problem = user_prompt.strip()
    literature_query = str(data.get("literature_query") or "").strip()
    raw_tags = data.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [x.strip() for x in raw_tags.split(",")]
    tags = []
    if isinstance(raw_tags, list):
        for value in raw_tags:
            tag = str(value).strip().lower()
            if tag and tag not in tags:
                tags.append(tag)
            if len(tags) >= 8:
                break
    return ProjectDraft(
        title=title[:120],
        project_id=project_id,
        description=description,
        experiment=experiment,
        problem=problem,
        literature_query=literature_query,
        tags=tags,
    )


def generate_project_draft(
    user_prompt: str,
    agent: Agent,
) -> tuple[ProjectDraft, LLMResponse, list[dict[str, str]]]:
    prompt = user_prompt.strip()
    if not prompt:
        raise ValueError("Proje promptu boş olamaz.")
    messages = [
        {
            "role": "user",
            "content": (
                "Aşağıdaki kullanıcı isteğinden proje taslağını üret. "
                "Araştırma problemi, kullanıcının tek promptundan bağımsız olarak çalışabilecek kadar açık olsun.\n\n"
                f"KULLANICI PROMPTU:\n{prompt}"
            ),
        }
    ]
    content, response = agent.respond(messages)
    return parse_project_draft(content, prompt), response, messages
