import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lab import Agent, Orchestrator, ResearchState, ResearchToolbox, TheoremResearchLab, Trace
from lab.openrouter_catalog import fetch_openrouter_models

load_dotenv()
RUNS_DIR = Path("runs")

FALLBACK_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "deepseek/deepseek-r1",
    "anthropic/claude-3.5-sonnet",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.3-70b-instruct",
]

ROLE_LIBRARY = {
    "Teorisyen": "Yaratıcı matematik/CS teorisyenisin. Test edilebilir fikir üret, varsayımları açıkça etiketle.",
    "Sceptik": "Adversarial hakemsin. Hata, gizli varsayım ve karşıörnek ara.",
    "Raporcu": "Tartışmayı tarafsız ve kısa bir araştırma raporuna dönüştür.",
    "Taraftar A": "A pozisyonunu mümkün olan en güçlü biçimde savun.",
    "Taraftar B": "B pozisyonunu mümkün olan en güçlü biçimde savun.",
    "Hakem": "Tartışmayı objektif ölçütlerle değerlendir.",
    "Araştırmacı": "Olgusal ve yapılandırılmış arka plan araştırması yap.",
    "Analist": "Araştırma notlarını eleştirel analiz et.",
    "Eleştirmen": "Analizdeki zayıf varsayım, eksik veri ve mantık hatalarını bul.",
    "Panelist": "Soruyu bağımsız ve özgün bir bakış açısından yanıtla.",
    "Sentezleyici": "Bağımsız yanıtları tutarlı tek bir cevapta birleştir.",
    "ResearchManager": "Araştırmayı yönet; doğru dalı seç, FAIL fikirleri kapalı tut ve tek sonraki görev ver.",
    "Theorist": "Küçük, test edilebilir lemma/construction/lower-bound fikri üret; varsayımı açıkça etiketle.",
    "AdversarialCritic": "Adayı çürütmeye çalış: karşıörnek, gizli varsayım, yanlış model ve asymptotic hata ara.",
    "VerificationEngineer": "LLM görüşünü ispat sayma; gerekli deterministic test, Z3, küçük-n veya formal proof'u belirle.",
    "LiteratureScout": "Literatür/novelty riskini tara; theorem içeriği uydurma.",
    "IndependentAuditor": "Sıfır-güven bağımsız denetçi ol; OPEN ile PROVEN'i kesin ayır.",
}

ROLE_TEMPS = {
    "Teorisyen": 0.8,
    "Sceptik": 0.3,
    "Raporcu": 0.4,
    "Taraftar A": 0.9,
    "Taraftar B": 0.9,
    "Hakem": 0.3,
    "Panelist": 0.7,
    "Sentezleyici": 0.4,
    "ResearchManager": 0.2,
    "Theorist": 0.8,
    "AdversarialCritic": 0.2,
    "VerificationEngineer": 0.1,
    "LiteratureScout": 0.1,
    "IndependentAuditor": 0.1,
}

ROLE_MODELS = {
    "Teorisyen": "deepseek/deepseek-r1",
    "Sceptik": "anthropic/claude-3.5-sonnet",
    "Taraftar A": "deepseek/deepseek-r1",
    "ResearchManager": "openai/gpt-4o",
    "Theorist": "deepseek/deepseek-r1",
    "AdversarialCritic": "anthropic/claude-3.5-sonnet",
    "VerificationEngineer": "openai/gpt-4o",
    "LiteratureScout": "openai/gpt-4o-mini",
    "IndependentAuditor": "google/gemini-2.0-flash-001",
}

ROLE_MODEL_ENV = {
    "ResearchManager": "LAB_MANAGER_MODEL",
    "Theorist": "LAB_PROPOSER_MODEL",
    "AdversarialCritic": "LAB_CRITIC_MODEL",
    "VerificationEngineer": "LAB_VERIFIER_MODEL",
    "LiteratureScout": "LAB_LITERATURE_MODEL",
    "IndependentAuditor": "LAB_AUDITOR_MODEL",
}

TROPICAL_PROBLEM = (
    "Let P_n be the simple s-t path provenance polynomial of K_n over the min-plus tropical semiring. "
    "Improve either the known O(n^3) circuit upper bound or the trivial Omega(n^2) lower bound, "
    "or isolate a new rigorous barrier/subclass result."
)

EXPERIMENTS = {
    "Teorem Araştırması": {
        "method": "theorem_lab",
        "slug": "theorem",
        "description": "Gerçek açık matematik/teorik CS problemi için: literatür + kalıcı ledger + deterministic doğrulama + critic + bağımsız audit. Bu proje için önerilen mod.",
        "roles": ["ResearchManager", "Theorist", "AdversarialCritic", "VerificationEngineer", "LiteratureScout", "IndependentAuditor"],
        "optional_roles": [],
        "param_label": "Tur sayısı",
        "param_default": 6,
        "prompt_label": "Araştırma problemi",
        "default_prompt": TROPICAL_PROBLEM,
    },
    "Araştırma Döngüsü": {
        "method": "research_loop",
        "slug": "research",
        "description": "Hızlı fikir↔eleştiri döngüsü. Kalıcı theorem state, literatür/tool zinciri ve bağımsız audit yok.",
        "roles": ["Teorisyen", "Sceptik", "Raporcu"],
        "optional_roles": ["Raporcu"],
        "param_label": "Tur sayısı",
        "param_default": 3,
        "prompt_label": "Araştırma problemi",
        "default_prompt": "Bir araştırma problemi için hipotez üret, eleştir ve revize et.",
    },
    "Tartışma": {
        "method": "debate",
        "slug": "debate",
        "description": "İki zıt görüş tartışır, opsiyonel hakem değerlendirir. Argüman testi içindir; theorem workflow'u değildir.",
        "roles": ["Taraftar A", "Taraftar B", "Hakem"],
        "optional_roles": ["Hakem"],
        "param_label": "Tur sayısı",
        "param_default": 2,
        "prompt_label": "Tartışma konusu",
        "default_prompt": "Yapay zeka geliştirme açık kaynak mı kapalı mı olmalı?",
    },
    "Zincir": {
        "method": "pipeline",
        "slug": "pipeline",
        "description": "Tek yönlü A→B→C akışı: araştır → analiz et → eleştir/düzenle. Geri beslemeli araştırma değildir.",
        "roles": ["Araştırmacı", "Analist", "Eleştirmen"],
        "optional_roles": [],
        "param_label": None,
        "param_default": 0,
        "prompt_label": "Görev",
        "default_prompt": "Bir konuyu araştır, analiz et ve eleştir.",
    },
    "Panel": {
        "method": "panel",
        "slug": "panel",
        "description": "Aynı soruya birkaç ajan bağımsız cevap verir, sentezleyici birleştirir. Model/fikir çeşitliliği için.",
        "roles": ["Panelist", "Panelist", "Panelist", "Sentezleyici"],
        "optional_roles": ["Sentezleyici"],
        "param_label": None,
        "param_default": 0,
        "prompt_label": "Soru",
        "default_prompt": "Aynı soruyu farklı uzman bakışlarıyla değerlendir.",
    },
}


@st.cache_data(ttl=600, show_spinner=False)
def load_openrouter_catalog() -> list[dict]:
    return [model.as_dict() | {"label": model.label} for model in fetch_openrouter_models()]


def model_catalog() -> tuple[list[str], dict[str, str], str | None]:
    try:
        data = load_openrouter_catalog()
        ids = [str(m["id"]) for m in data]
        labels = {str(m["id"]): str(m["label"]) for m in data}
        if ids:
            return ids, labels, None
    except Exception as exc:
        error = str(exc)
    else:
        error = "OpenRouter model kataloğu boş döndü."
    return FALLBACK_MODELS.copy(), {m: m for m in FALLBACK_MODELS}, error


def filter_models(model_ids: list[str], model_labels: dict[str, str], query: str) -> list[str]:
    needle = query.strip().casefold()
    if not needle:
        return list(model_ids)
    return [m for m in model_ids if needle in m.casefold() or needle in model_labels.get(m, "").casefold()]


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-")
    return value[:60] or "research"


def default_model(role: str) -> str:
    env_name = ROLE_MODEL_ENV.get(role)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return ROLE_MODELS.get(role, os.environ.get("LAB_MODEL", "openai/gpt-4o-mini"))


class ObservedTrace(Trace):
    def __init__(self, experiment: str, on_event=None):
        super().__init__(experiment)
        self.on_event = on_event

    def log(self, event_type: str, **data) -> None:
        super().log(event_type, **data)
        if self.on_event:
            self.on_event({"type": event_type, "_live_time": datetime.now().strftime("%H:%M:%S"), **data})


def build_sidebar(exp_name, model_ids, model_labels):
    exp = EXPERIMENTS[exp_name]
    st.sidebar.info(exp["description"])
    prompt = st.sidebar.text_area(exp["prompt_label"], value=exp["default_prompt"], height=145)
    param = None
    if exp["param_label"]:
        param = st.sidebar.number_input(
            exp["param_label"],
            min_value=1,
            max_value=100 if exp["method"] == "theorem_lab" else 10,
            value=exp["param_default"],
        )

    extras = {}
    if exp["method"] == "theorem_lab":
        extras["project_id"] = slugify(st.sidebar.text_input("Project ID", value="tropical-circuit"))
        extras["literature_query"] = st.sidebar.text_input(
            "Literatür arama sorgusu",
            value=os.environ.get("LAB_LITERATURE_QUERY", "tropical circuit reachability provenance lower bound"),
        )
        extras["checkpoint_every"] = st.sidebar.number_input(
            "Checkpoint sıklığı (tur)", min_value=1, max_value=20, value=2
        )

    agents, optional = [], {}
    role_counts = {}
    for i, role in enumerate(exp["roles"]):
        role_counts[role] = role_counts.get(role, 0) + 1
        display_role = role if role_counts[role] == 1 else f"{role} {role_counts[role]}"
        key = f"{exp_name}_{i}_{role_counts[role]}"
        is_optional = role in exp["optional_roles"]
        if is_optional and not st.sidebar.checkbox(
            f"{display_role} dahil et", value=True, key=f"inc_{key}"
        ):
            continue

        with st.sidebar.expander(display_role, expanded=exp["method"] == "theorem_lab"):
            sys_prompt = st.text_area(
                "Sistem promptu",
                ROLE_LIBRARY.get(role, ROLE_LIBRARY["Panelist"]),
                key=f"p_{key}",
                height=110,
            )
            wanted_default = default_model(role)
            query = st.text_input(
                "Model ara", placeholder="örn. 5.3, glm, kimi, flash", key=f"search_{key}"
            )
            choices = filter_models(model_ids, model_labels, query)
            if not query and wanted_default not in choices:
                choices.insert(0, wanted_default)
                model_labels.setdefault(wanted_default, wanted_default)
            if query:
                st.caption(f"{len(choices)} model eşleşti")

            if choices:
                preferred = wanted_default if wanted_default in choices else choices[0]
                model = st.selectbox(
                    "OpenRouter modeli",
                    choices,
                    index=choices.index(preferred),
                    format_func=lambda mid: model_labels.get(mid, mid),
                    key=f"m_{key}_{query.casefold()}",
                )
            else:
                st.warning("Eşleşen model yok. Aramayı değiştir veya manuel model ID gir.")
                model = wanted_default

            manual = st.text_input(
                "Manuel model ID (opsiyonel)",
                placeholder="örn. z-ai/glm-5.3",
                key=f"manual_{key}",
            ).strip()
            if manual:
                model = manual
            temp = st.slider(
                "Sıcaklık", 0.0, 1.5, ROLE_TEMPS.get(role, 0.7), 0.05, key=f"t_{key}"
            )
            st.caption(f"Kullanılacak model: `{model}`")

        cfg = {
            "role": role,
            "display_role": display_role,
            "prompt": sys_prompt,
            "model": model,
            "temp": temp,
        }
        if is_optional:
            optional[display_role] = cfg
        else:
            agents.append(cfg)
    return prompt, param, agents, optional, extras


def _agent(cfg):
    return Agent(
        name=cfg["display_role"],
        system_prompt=cfg["prompt"],
        model=cfg["model"],
        temperature=cfg["temp"],
    )


def _event_time(event: dict) -> str:
    if event.get("_live_time"):
        return str(event["_live_time"])
    raw = str(event.get("ts", ""))
    if "T" in raw:
        return raw.split("T", 1)[1][:8]
    return ""


def _event_summary(event: dict) -> str:
    kind = event.get("type", "event")
    if kind == "agent_start":
        return f"{event.get('agent')} başladı ({event.get('model')})"
    if kind == "llm_call":
        return f"{event.get('agent')} tamamlandı: {event.get('total_tokens', 0)} token"
    if kind == "tool_start":
        return f"Tool başladı: {event.get('request', {}).get('tool', '?')}"
    if kind == "tool_result":
        return f"Tool sonucu: {event.get('tool')} ok={event.get('ok')}"
    if kind == "state_change":
        return (
            f"State: {event.get('item_id')} {event.get('old_status', '')}→"
            f"{event.get('new_status', event.get('status', ''))}"
        )
    if kind == "checkpoint":
        return "Checkpoint kaydedildi"
    if kind == "literature_search":
        return f"Literatür: {len(event.get('results', []))} kayıt"
    return kind


def _tool_status(event: dict) -> tuple[str, str]:
    error = str(event.get("error") or "").strip()
    metadata = event.get("metadata") or {}
    metadata_status = str(metadata.get("status") or "").upper()
    if error:
        return "HATA", "error"
    if metadata_status == "COUNTEREXAMPLE":
        return "COUNTEREXAMPLE", "error"
    if bool(event.get("ok")):
        return "PASS", "success"
    return "FAIL", "error"


def render_timeline_event(target, event: dict) -> None:
    """Render non-stream events. Stream/runtime events are handled by LiveTimelineRenderer."""
    kind = str(event.get("type", "event"))
    clock = _event_time(event)
    stamp = f"`{clock}` " if clock else ""

    if kind in {"agent_stream", "runtime_state", "agent_start", "llm_call", "agent_error", "agent_retry"}:
        return

    if kind == "run_config":
        target.markdown(f"{stamp}**Sistem** — çalışma yapılandırması yüklendi")
        with target.expander("Run config", expanded=False):
            st.json({k: v for k, v in event.items() if k not in {"type", "ts", "_live_time"}})
        return

    if kind == "problem_frozen":
        target.markdown(f"{stamp}**Problem** — araştırma problemi donduruldu")
        with target.expander("Frozen problem", expanded=False):
            st.code(str(event.get("problem", "")), language=None)
        return

    if kind == "iteration_start":
        target.markdown(
            f"{stamp}**Tur {event.get('iteration')}** — başladı · sonraki hedef: "
            f"{event.get('next_task', '')}"
        )
        return

    if kind == "iteration_end":
        target.markdown(
            f"{stamp}**Tur {event.get('iteration')}** — tamamlandı · `{event.get('item_id', '')}` · "
            f"**{event.get('status', '')}** · karar `{event.get('decision', '')}`"
        )
        if event.get("next_task"):
            target.caption(f"Sonraki görev: {event['next_task']}")
        return

    if kind == "literature_search_start":
        target.markdown(f"{stamp}**Literatür** — arama başladı: `{event.get('query', '')}`")
        return

    if kind == "literature_search":
        results = event.get("results", []) or []
        target.markdown(f"{stamp}**Literatür** — {len(results)} aday kayıt bulundu")
        if results:
            with target.expander("Bulunan yayınlar", expanded=False):
                for i, paper in enumerate(results, 1):
                    st.write(f"{i}. {paper.get('title', '?')} ({paper.get('year', '?')})")
                    if paper.get("url"):
                        st.caption(str(paper["url"]))
        return

    if kind == "literature_search_error":
        target.error(f"{clock} Literatür araması hata verdi: {event.get('error', '')}")
        return

    if kind == "tool_start":
        request = event.get("request", {}) or {}
        tool = str(request.get("tool", "tool"))
        target.markdown(f"{stamp}**Araç** · `{tool}` — çalışmaya başladı")
        with target.expander(f"{tool} · input", expanded=False):
            st.json(request)
        return

    if kind == "tool_result":
        tool = str(event.get("tool", "tool"))
        label, tone = _tool_status(event)
        target.markdown(f"{stamp}**Araç** · `{tool}` — **{label}**")
        with target.expander(f"{tool} · çıktı", expanded=label in {"HATA", "COUNTEREXAMPLE"}):
            if event.get("output"):
                st.code(str(event["output"]), language=None)
            if event.get("error"):
                st.error(str(event["error"]))
            if event.get("metadata"):
                st.json(event["metadata"])
        return

    if kind == "state_change":
        item_id = str(event.get("item_id", "state"))
        action = str(event.get("action", "update"))
        old_status = event.get("old_status")
        new_status = event.get("new_status", event.get("status"))
        if action == "create":
            target.markdown(
                f"{stamp}**Research State** — `{item_id}` oluşturuldu · "
                f"**{new_status or event.get('status', '')}**"
            )
            if event.get("claim"):
                target.caption(str(event["claim"]))
        elif action == "counterexample":
            target.markdown(
                f"{stamp}**Counterexample** — `{event.get('target_id', '')}` için karşıörnek kaydedildi"
            )
            with target.expander("Counterexample ayrıntısı", expanded=False):
                st.json(event.get("detail"))
        else:
            target.markdown(
                f"{stamp}**Research State** — `{item_id}` · `{old_status or '?'}` → "
                f"**{new_status or '?'}**"
                + (f" · karar `{event.get('decision')}`" if event.get("decision") else "")
            )
            if event.get("reason"):
                target.caption(str(event["reason"]))
        return

    if kind == "checkpoint":
        label = "Final checkpoint" if event.get("final") else f"Checkpoint {event.get('iteration', '')}"
        target.markdown(f"{stamp}**{label}** — kalıcı olarak kaydedildi")
        with target.expander(f"{label} · audit", expanded=False):
            if event.get("path"):
                st.caption(str(event["path"]))
            if event.get("audit"):
                st.code(str(event["audit"]), language=None)
        return

    if kind == "step_reused":
        target.caption(
            f"♻️ `{event.get('step_key', '')}` tamamlanmış kayıttan yeniden kullanıldı"
        )
        return

    if kind == "run_paused":
        target.warning(f"Araştırma hata nedeniyle beklemeye alındı: {event.get('error', '')}")
        return

    if kind == "run_stopped":
        target.warning("Araştırma kullanıcı isteğiyle durduruldu; tamamlanan adımlar korundu.")
        return

    if kind in {"agent_retry"}:
        return

    target.caption(
        f"{stamp}{kind}: "
        + json.dumps(
            {k: v for k, v in event.items() if k not in {"type", "ts", "_live_time"}},
            ensure_ascii=False,
        )[:500]
    )


class LiveTimelineRenderer:
    """OpenCode-like readable timeline while preserving raw events in trace.jsonl."""

    def __init__(self, target, *, live: bool):
        self.target = target
        self.live = live
        self.runtime_slot = target.empty()
        self.cards: dict[str, dict] = {}
        self.latest_by_agent: dict[str, str] = {}

    def _key(self, event: dict) -> str:
        explicit = str(event.get("step_key") or "").strip()
        if explicit:
            return explicit
        agent = str(event.get("agent") or "Agent")
        return self.latest_by_agent.get(agent, f"{agent}:{len(self.cards) + 1}")

    def _ensure_card(self, event: dict) -> dict:
        key = self._key(event)
        if key in self.cards:
            return self.cards[key]

        agent = str(event.get("agent") or "Agent")
        model = str(event.get("model") or "")
        card = self.target.container(border=True)
        header = card.empty()
        header.markdown(f"⏳ **{agent}** · `{model}` — çalışıyor")
        reasoning_tab, answer_tab, task_tab = card.tabs(["🧠 Reasoning", "✍️ Cevap", "📋 Görev"])
        with reasoning_tab:
            reasoning_slot = st.empty()
            reasoning_slot.caption("Provider reasoning gönderirse burada canlı görünecek.")
        with answer_tab:
            answer_slot = st.empty()
            answer_slot.caption("Yanıt bekleniyor…")
        with task_tab:
            if event.get("system_prompt"):
                st.markdown("**System prompt**")
                st.code(str(event.get("system_prompt") or ""), language=None)
            if event.get("prompt"):
                st.markdown("**User/task prompt**")
                st.code(str(event.get("prompt") or ""), language=None)
            if not event.get("system_prompt") and not event.get("prompt"):
                st.caption("Görev ayrıntısı bu event içinde yok.")
        footer = card.empty()
        attempt = event.get("attempt")
        footer.caption(f"Adım: `{key}`" + (f" · deneme {attempt}" if attempt else ""))

        state = {
            "key": key,
            "agent": agent,
            "model": model,
            "header": header,
            "reasoning_slot": reasoning_slot,
            "answer_slot": answer_slot,
            "footer": footer,
            "reasoning": "",
            "content": "",
            "last_reasoning_render": 0.0,
            "last_content_render": 0.0,
            "complete": False,
        }
        self.cards[key] = state
        self.latest_by_agent[agent] = key
        return state

    def _render_buffer(self, state: dict, channel: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if channel == "reasoning":
            text = state["reasoning"]
            last_key = "last_reasoning_render"
            slot = state["reasoning_slot"]
        else:
            text = state["content"]
            last_key = "last_content_render"
            slot = state["answer_slot"]
        if not text:
            return
        if not force and self.live and now - float(state[last_key]) < 0.08:
            return
        state[last_key] = now
        slot.markdown(text)

    def _runtime(self, event: dict) -> None:
        status = str(event.get("status") or "")
        iteration = int(event.get("current_iteration", 0) or 0)
        step = str(event.get("current_step") or "-")
        completed = int(event.get("completed_iterations", 0) or 0)
        self.runtime_slot.caption(
            f"**Durum:** `{status}` · **Tur:** {iteration} · **Tamamlanan:** {completed} · "
            f"**Aktif adım:** `{step}`"
        )

    def _start(self, event: dict) -> None:
        state = self._ensure_card(event)
        agent = str(event.get("agent") or state["agent"])
        model = str(event.get("model") or state["model"])
        attempt = event.get("attempt")
        state["agent"] = agent
        state["model"] = model
        state["header"].markdown(
            f"⏳ **{agent}** · `{model}` — çalışıyor"
            + (f" · deneme {attempt}" if attempt and int(attempt) > 1 else "")
        )
        self.latest_by_agent[agent] = state["key"]

    def _stream(self, event: dict) -> None:
        state = self._ensure_card(event)
        channel = str(event.get("channel") or "")
        delta = event.get("delta")
        if channel == "reasoning" and isinstance(delta, str):
            state["reasoning"] += delta
            if self.live:
                self._render_buffer(state, "reasoning")
        elif channel == "content" and isinstance(delta, str):
            state["content"] += delta
            if self.live:
                self._render_buffer(state, "content")
        # reasoning_details are intentionally kept in raw logs instead of cluttering this view.

    def _complete(self, event: dict) -> None:
        agent = str(event.get("agent") or "Agent")
        key = self.latest_by_agent.get(agent)
        state = self.cards.get(key) if key else None
        if state is None:
            state = self._ensure_card(event)

        full_reasoning = str(event.get("provider_reasoning") or "")
        full_content = str(event.get("output") or "")
        if full_reasoning:
            state["reasoning"] = full_reasoning
        if full_content:
            state["content"] = full_content
        self._render_buffer(state, "reasoning", force=True)
        self._render_buffer(state, "content", force=True)

        reasoning_tokens = int(event.get("reasoning_tokens", 0) or 0)
        if not state["reasoning"] and reasoning_tokens:
            state["reasoning_slot"].caption(
                f"Model {reasoning_tokens:,} reasoning token kullandı ancak provider reasoning metnini expose etmedi."
            )
        elif not state["reasoning"]:
            state["reasoning_slot"].caption("Bu model/provider görünür reasoning metni göndermedi.")

        cost = event.get("cost_usd")
        cost_text_value = f"${float(cost):.6f}" if cost is not None else "ücret N/A"
        total_tokens = int(event.get("total_tokens", 0) or 0)
        latency = float(event.get("latency_s", 0) or 0)
        state["header"].markdown(
            f"✅ **{agent}** · `{event.get('model', state['model'])}` — tamamlandı · "
            f"{total_tokens:,} token · {cost_text_value} · {latency:.1f} sn"
        )
        state["footer"].caption(
            f"input={int(event.get('prompt_tokens', 0) or 0):,} · "
            f"output={int(event.get('completion_tokens', 0) or 0):,} · "
            f"reasoning={reasoning_tokens:,} · cached={int(event.get('cached_tokens', 0) or 0):,} · "
            f"adım `{state['key']}`"
        )
        state["complete"] = True

    def _error(self, event: dict) -> None:
        state = self._ensure_card(event)
        retrying = bool(event.get("retrying"))
        error = str(event.get("error") or "Bilinmeyen hata")
        if retrying:
            state["header"].markdown(
                f"⚠️ **{state['agent']}** · `{state['model']}` — çağrı hatası, yeniden denenecek"
            )
            state["footer"].warning(error)
        else:
            state["header"].markdown(
                f"❌ **{state['agent']}** · `{state['model']}` — bu adım tamamlanamadı"
            )
            state["footer"].error(error)

    def _retry(self, event: dict) -> None:
        key = str(event.get("step_key") or "")
        state = self.cards.get(key)
        if state:
            state["footer"].caption(
                f"Geçici hata sonrası {event.get('wait_s', '?')} sn bekleniyor · "
                f"sonraki deneme {event.get('next_attempt', '?')}"
            )

    def handle(self, event: dict) -> None:
        kind = str(event.get("type") or "event")
        if kind == "runtime_state":
            self._runtime(event)
            return
        if kind == "agent_start":
            self._start(event)
            return
        if kind == "agent_stream":
            self._stream(event)
            return
        if kind == "llm_call":
            self._complete(event)
            return
        if kind == "agent_error":
            self._error(event)
            return
        if kind == "agent_retry":
            self._retry(event)
            return
        render_timeline_event(self.target, event)

    def finalize(self) -> None:
        for state in self.cards.values():
            self._render_buffer(state, "reasoning", force=True)
            self._render_buffer(state, "content", force=True)


def load_trace_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _read_runtime(state: ResearchState) -> dict:
    path = state.root / "runtime.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def execute(exp_name, prompt, param, agents, optional, extras):
    exp = EXPERIMENTS[exp_name]
    steps = []

    with st.chat_message("user"):
        st.write(prompt)
    st.subheader("Canlı Araştırma Akışı")
    timeline = st.container(border=True)
    renderer = LiveTimelineRenderer(timeline, live=True)
    run_status = st.status("Araştırma çalışıyor...", expanded=False)
    theorem_state = None

    def on_event(event: dict):
        if event.get("type") not in {"agent_stream", "runtime_state"}:
            steps.append(_event_summary(event))
        renderer.handle(event)

    trace = ObservedTrace(exp["slug"], on_event=on_event)
    try:
        a_objs = [_agent(c) for c in agents]
        o_objs = {r: _agent(c) for r, c in optional.items()}
        method = exp["method"]
        if method == "theorem_lab":
            by_role = {cfg["role"]: _agent(cfg) for cfg in agents}
            theorem_state = ResearchState(f"research_state/{extras['project_id']}")
            trace.log(
                "run_config",
                experiment=exp_name,
                project_id=extras["project_id"],
                iterations=int(param),
                models={r: a.model for r, a in by_role.items()},
            )
            result = TheoremResearchLab(trace, theorem_state, toolbox=ResearchToolbox()).run(
                prompt,
                manager=by_role["ResearchManager"],
                proposer=by_role["Theorist"],
                critic=by_role["AdversarialCritic"],
                verifier=by_role["VerificationEngineer"],
                literature_agent=by_role["LiteratureScout"],
                auditor=by_role["IndependentAuditor"],
                iterations=int(param),
                literature_query=extras.get("literature_query") or None,
                checkpoint_every=int(extras.get("checkpoint_every", 2)),
            )
        else:
            orch = Orchestrator(trace)
            if method == "research_loop":
                result = orch.research_loop(
                    prompt,
                    a_objs[0],
                    a_objs[1],
                    iterations=int(param),
                    synthesizer=o_objs.get("Raporcu"),
                )
            elif method == "debate":
                result = orch.debate(prompt, a_objs[:2], rounds=int(param), judge=o_objs.get("Hakem"))
            elif method == "pipeline":
                result = orch.pipeline(prompt, a_objs)
            else:
                result = orch.panel(prompt, a_objs, synthesizer=o_objs.get("Sentezleyici"))
    except Exception as exc:
        run_status.update(label="Araştırma hata verdi", state="error", expanded=True)
        st.error(f"Deney başarısız: {exc}")
        return None
    finally:
        renderer.finalize()
        summary_path = trace.close()

    runtime = _read_runtime(theorem_state) if theorem_state is not None else {}
    runtime_status = str(runtime.get("status") or "")
    if runtime_status == "PAUSED_ERROR":
        run_status.update(label="Araştırma hata nedeniyle beklemede", state="error", expanded=True)
    elif runtime_status == "STOPPED":
        run_status.update(label="Araştırma güvenli biçimde durduruldu", state="complete")
    else:
        run_status.update(label="Araştırma tamamlandı", state="complete")

    return {
        "exp": exp_name,
        "result": result,
        "summary": json.loads(summary_path.read_text(encoding="utf-8")),
        "run_dir": str(trace.run_dir),
        "steps": steps,
    }


def cost_text(summary):
    return f"{'' if summary.get('cost_complete', False) else '≥'}${float(summary.get('total_cost_usd', 0.0)):.6f}"


def usage_rows(summary):
    return [
        {
            "Ajan": name,
            "Model": ", ".join(v.get("models", [])),
            "Çağrı": v.get("calls", 0),
            "Input": v.get("prompt_tokens", 0),
            "Output": v.get("completion_tokens", 0),
            "Reasoning": v.get("reasoning_tokens", 0),
            "Cached": v.get("cached_tokens", 0),
            "Toplam": v.get("total_tokens", 0),
            "Ücret ($)": round(float(v.get("cost_usd", 0)), 8),
            "Süre (sn)": round(float(v.get("latency_s", 0)), 2),
        }
        for name, v in summary.get("agents", {}).items()
    ]


def summary_metrics(summary):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Toplam çağrı", summary.get("total_calls", 0))
    c2.metric("Toplam token", f"{summary.get('total_tokens', 0):,}")
    c3.metric("Toplam ücret", cost_text(summary))
    c4.metric("Geçen süre", f"{float(summary.get('wall_time_s', 0)):.1f} sn")


def render_result(last):
    st.divider()
    summary_metrics(last["summary"])
    rows = usage_rows(last["summary"])
    if rows:
        st.subheader("Ajan bazında kullanım")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(f"Kayıt klasörü: {last['run_dir']}")
    st.subheader("Sonuç")
    st.markdown(last["result"])


def render_history():
    traces = (
        sorted(RUNS_DIR.glob("*/trace.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if RUNS_DIR.exists()
        else []
    )
    if not traces:
        st.info("Henüz kayıt yok.")
        return
    selected = st.selectbox("Deney kaydı", [p.parent for p in traces], format_func=lambda p: p.name)
    summary_file = selected / "summary.json"
    if summary_file.exists():
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        summary_metrics(summary)
        rows = usage_rows(summary)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    all_events = load_trace_events(selected / "trace.jsonl")
    llm_events = [ev for ev in all_events if ev.get("type") == "llm_call"]
    if llm_events:
        st.subheader("Çağrı bazında kullanım")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Ajan": ev.get("agent"),
                        "Model": ev.get("model"),
                        "Input": ev.get("prompt_tokens", 0),
                        "Output": ev.get("completion_tokens", 0),
                        "Reasoning": ev.get("reasoning_tokens", 0),
                        "Toplam": ev.get("total_tokens", 0),
                        "Ücret ($)": ev.get("cost_usd"),
                        "Süre (sn)": ev.get("latency_s", 0),
                    }
                    for ev in llm_events
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Tam Araştırma Timeline'ı")
    st.caption(
        "Okunabilir görünüm: stream parçaları ajan kartlarında birleştirilir. Tam ham JSON için Ham Loglar sayfasını kullan."
    )
    timeline = st.container(border=True)
    renderer = LiveTimelineRenderer(timeline, live=False)
    for event in all_events:
        renderer.handle(event)
    renderer.finalize()


def main():
    st.title("LLM Araştırma Laboratuvarı")
    st.sidebar.header("Deney Ayarları")

    api_ok = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    if not api_ok:
        st.sidebar.error("API anahtarı bulunamadı. `.env` içine OPENROUTER_API_KEY ekle.")

    model_ids, model_labels, catalog_error = model_catalog()
    if catalog_error:
        st.sidebar.warning(
            "Canlı OpenRouter listesi alınamadı; fallback liste gösteriliyor. Manuel model ID yine kullanılabilir."
        )
    else:
        st.sidebar.caption(f"OpenRouter kataloğu: {len(model_ids)} text modeli")
    if st.sidebar.button("Model listesini yenile"):
        load_openrouter_catalog.clear()
        st.rerun()

    exp_name = st.sidebar.selectbox("Deney tipi", list(EXPERIMENTS))
    prompt, param, agents, optional, extras = build_sidebar(exp_name, model_ids, model_labels)

    tab_run, tab_hist = st.tabs(["Deney", "Geçmiş Kayıtlar"])
    with tab_run:
        if st.button("Deneyi Çalıştır", type="primary", disabled=not api_ok, use_container_width=True):
            if not prompt.strip():
                st.warning("Lütfen bir problem/konu gir.")
            else:
                data = execute(exp_name, prompt, param, agents, optional, extras)
                if data:
                    st.session_state["last"] = data
        last = st.session_state.get("last")
        if last and last["exp"] == exp_name:
            render_result(last)
    with tab_hist:
        render_history()


if __name__ == "__main__":
    main()
