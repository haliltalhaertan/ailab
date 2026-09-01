from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

from lab.agent import Agent
from lab.client import LLMResponse
from lab.trace import get_active_trace


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
- JSON stringlerinde LaTeX kullanman gerekirse backslash karakterlerini JSON standardına göre çift kaçır
  (ör. \\Omega metinde \\\\Omega olarak bulunmalı). Daha güvenlisi, proje metadata alanlarında LaTeX
  komutları yerine düz Unicode/metin kullanmaktır.

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


def _strip_code_fence(text: str) -> str:
    raw = text.strip().lstrip("\ufeff")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json|javascript|js)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw.strip()


def _object_slice(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return text
    return text[start : end + 1]


def _repair_string_escapes(text: str) -> str:
    """Repair common almost-JSON emitted by LLMs without changing field content.

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
                out.extend(("\\", nxt))
                i += 2
                continue
            if nxt == "u" and i + 5 < len(text) and all(c in hex_digits for c in text[i + 2 : i + 6]):
                out.append(text[i : i + 6])
                i += 6
                continue
            # Invalid JSON escape (for example \Omega, \(, \Theta, \underbrace).
            # Emit a JSON-escaped literal backslash and process the next char normally.
            out.append("\\\\")
            i += 1
            continue

        code = ord(ch)
        if code < 0x20:
            controls = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}
            out.append(controls.get(ch, f"\\u{code:04x}"))
        else:
            out.append(ch)
        i += 1

    return "".join(out)


def _repair_json_text(text: str) -> str:
    repaired = _repair_string_escapes(text)
    # A frequent LLM formatting mistake: a trailing comma before ] or }.
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def _extract_json(text: str) -> dict[str, Any]:
    raw = _strip_code_fence(text)
    sliced = _object_slice(raw)
    candidates: list[str] = []
    for candidate in (raw, sliced, _repair_json_text(sliced)):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(value, dict):
            raise ValueError("ProjectPlanner çıktısı JSON object olmalı.")
        return value

    if "{" not in raw or "}" not in raw:
        raise ValueError("ProjectPlanner geçerli JSON döndürmedi.")
    detail = f" (satır {last_error.lineno}, sütun {last_error.colno})" if last_error else ""
    raise ValueError(f"ProjectPlanner JSON çıktısı ayrıştırılamadı{detail}.")


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


def _initial_messages(prompt: str) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": (
                "Aşağıdaki kullanıcı isteğinden proje taslağını üret. "
                "Araştırma problemi, kullanıcının tek promptundan bağımsız olarak çalışabilecek kadar açık olsun.\n\n"
                f"KULLANICI PROMPTU:\n{prompt}"
            ),
        }
    ]


def _repair_messages(user_prompt: str, invalid_output: str, parse_error: Exception) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": (
                "Önceki proje taslağı çıktın JSON olarak ayrıştırılamadı. İçeriğin anlamını koruyarak "
                "çıktıyı SADECE geçerli JSON object olarak yeniden yaz. Markdown/code fence/açıklama ekleme. "
                "JSON stringlerinde ham LaTeX backslash kullanma: mümkünse Ω, Θ, ω gibi Unicode semboller "
                "ve düz metin kullan; backslash gerekiyorsa JSON için çift kaçır. Trailing comma kullanma.\n\n"
                f"AYRIŞTIRMA HATASI:\n{parse_error}\n\n"
                f"ORİJİNAL KULLANICI PROMPTU:\n{user_prompt}\n\n"
                f"GEÇERSİZ ÇIKTI:\n{invalid_output}"
            ),
        }
    ]


PlannerCallCallback = Callable[[str, list[dict[str, str]], LLMResponse], None]


def _trace_failed_initial_call(
    agent: Agent,
    messages: list[dict[str, str]],
    response: LLMResponse,
    error: Exception,
) -> None:
    """Preserve the failed paid call when the caller uses the legacy return API."""

    trace = get_active_trace()
    if trace is None:
        return
    trace.agent_call(agent.name, response.model, agent.temperature, messages, response)
    trace.log(
        "project_planner_parse_error",
        stage="initial",
        agent=agent.name,
        model=response.model,
        error=str(error),
    )
    trace.log("project_planner_repair_start", agent=agent.name, model=agent.model)


def generate_project_draft(
    user_prompt: str,
    agent: Agent,
    *,
    on_call: PlannerCallCallback | None = None,
) -> tuple[ProjectDraft, LLMResponse, list[dict[str, str]]]:
    """Generate a project draft, automatically repairing malformed JSON once.

    ``on_call`` is invoked for every paid LLM call (stage ``initial`` or ``repair``),
    allowing custom callers to retain both attempts. With the legacy UI path, a
    failed initial call is written to the active Trace automatically; the caller
    then records the returned successful repair call as before. The public return
    shape remains backward compatible.
    """

    prompt = user_prompt.strip()
    if not prompt:
        raise ValueError("Proje promptu boş olamaz.")

    messages = _initial_messages(prompt)
    content, response = agent.respond(messages)
    if on_call:
        on_call("initial", messages, response)
    try:
        draft = parse_project_draft(content, prompt)
        return draft, response, messages
    except ValueError as first_error:
        if on_call is None:
            _trace_failed_initial_call(agent, messages, response, first_error)
        repair_messages = _repair_messages(prompt, content, first_error)
        repaired_content, repaired_response = agent.respond(repair_messages)
        if on_call:
            on_call("repair", repair_messages, repaired_response)
        try:
            draft = parse_project_draft(repaired_content, prompt)
        except ValueError as second_error:
            raise ValueError(
                "ProjectPlanner çıktısı iki denemede de geçerli JSON'a dönüştürülemedi. "
                f"Son hata: {second_error}"
            ) from second_error
        return draft, repaired_response, repair_messages
