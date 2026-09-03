from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lab.openrouter_catalog import fetch_openrouter_models
from lab.project_manager import ProjectInfo, ProjectManager
from lab.prompts import ROLE_LIBRARY as THEOREM_ROLE_LIBRARY
from lab.reasoning_settings import API_TO_UI, UI_LEVELS, UI_TO_API, get_reasoning_effort
from lab.ui_live import render_now_and_timeline
from lab.ui_model import (
    cost_text,
    event_summary,
    filter_models,
    load_live_run_events,
    load_run_events,
    runs_for_project,
    tool_status,
    usage_rows,
)
from lab.worker_launcher import build_request_from_ui, launch_worker, write_worker_request

load_dotenv()
RUNS_DIR = Path("runs")
PROJECTS = ProjectManager()

FALLBACK_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "deepseek/deepseek-r1",
    "z-ai/glm-5.3-flash",
    "google/gemini-2.5-pro",
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
    **THEOREM_ROLE_LIBRARY,
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
    "Sceptik": "z-ai/glm-5.3-flash",
    "Taraftar A": "deepseek/deepseek-r1",
    "ResearchManager": "openai/gpt-4o",
    "Theorist": "deepseek/deepseek-r1",
    "AdversarialCritic": "z-ai/glm-5.3-flash",
    "VerificationEngineer": "openai/gpt-4o",
    "LiteratureScout": "openai/gpt-4o-mini",
    "IndependentAuditor": "google/gemini-2.5-pro",
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
        "description": "Uzun theorem run'ı ayrı worker process'te çalışır; canlı akışı bu sayfada izleyebilirsin.",
        "roles": [
            "ResearchManager",
            "Theorist",
            "AdversarialCritic",
            "VerificationEngineer",
            "LiteratureScout",
            "IndependentAuditor",
        ],
        "optional_roles": [],
        "param_label": "Tur sayısı",
        "param_default": 6,
        "prompt_label": "Araştırma problemi",
        "default_prompt": TROPICAL_PROBLEM,
    },
    "Araştırma Döngüsü": {
        "method": "research_loop",
        "slug": "research",
        "description": "Hızlı fikir-eleştiri döngüsü; kalıcı theorem workflow'u değildir.",
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
        "description": "İki zıt görüş tartışır, opsiyonel hakem değerlendirir.",
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
        "description": "Tek yönlü A→B→C akışı.",
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
        "description": "Bağımsız yanıtlar + sentez.",
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


def default_model(role: str) -> str:
    env_name = ROLE_MODEL_ENV.get(role)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return ROLE_MODELS.get(role, os.environ.get("LAB_MODEL", "openai/gpt-4o-mini"))


def build_sidebar(exp_name: str, model_ids: list[str], model_labels: dict[str, str], active: ProjectInfo):
    exp = EXPERIMENTS[exp_name]
    st.sidebar.info(exp["description"])
    prompt_default = active.problem if active.problem and exp["method"] == "theorem_lab" else exp["default_prompt"]
    prompt = st.sidebar.text_area(exp["prompt_label"], value=prompt_default, height=145)
    param = None
    if exp["param_label"]:
        param = st.sidebar.number_input(
            exp["param_label"],
            min_value=1,
            max_value=100 if exp["method"] == "theorem_lab" else 10,
            value=exp["param_default"],
        )
    extras = {"project_id": active.project_id, "project_title": active.title}
    if exp["method"] == "theorem_lab":
        extras["literature_query"] = st.sidebar.text_input(
            "Literatür arama sorgusu",
            value=active.literature_query
            or os.environ.get("LAB_LITERATURE_QUERY", "tropical circuit reachability provenance lower bound"),
        )
        extras["checkpoint_every"] = st.sidebar.number_input(
            "Checkpoint sıklığı (tur)", min_value=1, max_value=20, value=2
        )

    agents: list[dict] = []
    optional: dict[str, dict] = {}
    role_counts: dict[str, int] = {}
    for i, role in enumerate(exp["roles"]):
        role_counts[role] = role_counts.get(role, 0) + 1
        display_role = role if role_counts[role] == 1 else f"{role} {role_counts[role]}"
        key = f"{exp_name}_{i}_{role_counts[role]}"
        is_optional = role in exp["optional_roles"]
        if is_optional and not st.sidebar.checkbox(f"{display_role} dahil et", value=True, key=f"inc_{key}"):
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
                "Model ara",
                placeholder="örn. 5.3, glm, kimi, flash",
                key=f"search_{key}",
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
                st.warning("Eşleşen model yok. Manuel model ID girebilirsin.")
                model = wanted_default
            manual = st.text_input("Manuel model ID (opsiyonel)", key=f"manual_{key}").strip()
            if manual:
                model = manual
            temp = st.slider(
                "Sıcaklık",
                0.0,
                1.5,
                ROLE_TEMPS.get(role, 0.7),
                0.05,
                key=f"t_{key}",
            )
            default_effort = get_reasoning_effort(display_role)
            default_effort_label = API_TO_UI.get(default_effort, "Provider default")
            effort_label = st.selectbox(
                "Reasoning effort",
                UI_LEVELS,
                index=UI_LEVELS.index(default_effort_label),
                key=f"effort_{key}",
            )
            reasoning_effort = UI_TO_API[effort_label]
            if reasoning_effort == "xhigh":
                st.info("xhigh reasoning tek çağrıda 5–10 dakika sürebilir; canlı panelde ilerlemeyi göreceksin.")
            st.caption(f"Kullanılacak model: `{model}`")
        cfg = {
            "role": role,
            "display_role": display_role,
            "prompt": sys_prompt,
            "model": model,
            "temp": temp,
            "reasoning_effort": reasoning_effort,
        }
        if is_optional:
            optional[display_role] = cfg
        else:
            agents.append(cfg)
    return prompt, param, agents, optional, extras


def _event_time(event: dict) -> str:
    raw = str(event.get("ts") or "")
    return raw.split("T", 1)[1][:8] if "T" in raw else ""


def render_timeline_event(target, event: dict) -> None:
    kind = str(event.get("type", "event"))
    if kind in {
        "agent_stream",
        "runtime_state",
        "agent_start",
        "llm_call",
        "agent_error",
        "agent_retry",
        "stage",
        "stage_end",
    }:
        return
    stamp = f"`{_event_time(event)}` " if _event_time(event) else ""
    if kind == "iteration_start":
        target.markdown(f"{stamp}**Tur {event.get('iteration')}** — başladı · {event.get('next_task', '')}")
    elif kind == "iteration_end":
        target.markdown(
            f"{stamp}**Tur {event.get('iteration')}** — `{event.get('item_id', '')}` · **{event.get('status', '')}**"
        )
    elif kind == "status_downgraded_by_guard":
        target.warning(
            f"Evidence guard: `{event.get('requested')}` → `{event.get('granted')}` · {event.get('reason', '')}"
        )
    elif kind == "literature_search_inconclusive":
        target.warning("Literatür taraması sonuçsuz kaldı; novelty hakkında sonuç çıkarılmadı.")
    elif kind == "literature_search":
        target.markdown(f"{stamp}**Literatür** — {len(event.get('results', []))} aday kayıt")
    elif kind == "tool_start":
        request = event.get("request") or {}
        target.markdown(f"{stamp}**Araç** · `{request.get('tool', '')}` — çalışıyor")
    elif kind == "tool_result":
        label, _ = tool_status(event)
        target.markdown(f"{stamp}**Araç** · `{event.get('tool', '')}` — **{label}**")
        with target.expander(f"{event.get('tool', 'tool')} · çıktı", expanded=False):
            if event.get("output"):
                st.code(str(event["output"]), language=None)
            if event.get("error"):
                st.error(str(event["error"]))
            if event.get("metadata"):
                st.json(event["metadata"])
    elif kind == "state_change":
        target.markdown(
            f"{stamp}**Research State** — `{event.get('item_id', '')}` · "
            f"`{event.get('old_status', '')}` → **{event.get('new_status', event.get('status', ''))}**"
        )
    elif kind == "checkpoint":
        target.markdown(f"{stamp}**Checkpoint** — kaydedildi")
    elif kind == "step_reused":
        target.caption(f"♻️ `{event.get('step_key', '')}` cache'den kullanıldı")
    elif kind == "run_stopped":
        target.warning("Araştırma kullanıcı isteğiyle durduruldu.")
    elif kind == "run_paused":
        target.warning(f"Araştırma beklemeye alındı: {event.get('error', '')}")
    else:
        target.caption(f"{stamp}{event_summary(event)}")


class LiveTimelineRenderer:
    """OpenCode-style readable agent cards; raw events stay in the log files."""

    REASONING_HEIGHT = 360
    ANSWER_HEIGHT = 360
    TASK_HEIGHT = 260

    def __init__(self, target):
        self.target = target
        self.cards: dict[str, dict] = {}
        self.latest_by_agent: dict[str, str] = {}

    def _key(self, event: dict) -> str:
        explicit = str(event.get("step_key") or "").strip()
        if explicit:
            return explicit
        agent = str(event.get("agent") or "Agent")
        return self.latest_by_agent.get(agent, f"{agent}:{len(self.cards) + 1}")

    def _card(self, event: dict) -> dict:
        key = self._key(event)
        if key in self.cards:
            return self.cards[key]
        agent = str(event.get("agent") or "Agent")
        model = str(event.get("model") or "")
        card = self.target.container(border=True)
        header = card.empty()
        header.markdown(f"⏳ **{agent}** · `{model}` — çalışıyor")
        rtab, atab, ttab = card.tabs(["🧠 Reasoning", "✍️ Cevap", "📋 Görev"])
        with rtab:
            with st.container(height=self.REASONING_HEIGHT, border=False):
                rslot = st.empty()
                rslot.caption("Provider reasoning gönderirse burada canlı görünecek.")
        with atab:
            with st.container(height=self.ANSWER_HEIGHT, border=False):
                aslot = st.empty()
                aslot.caption("Yanıt bekleniyor…")
        with ttab:
            with st.container(height=self.TASK_HEIGHT, border=False):
                if event.get("system_prompt"):
                    st.markdown("**System prompt**")
                    st.code(str(event.get("system_prompt") or ""), language=None)
                if event.get("prompt"):
                    st.markdown("**User/task prompt**")
                    st.code(str(event.get("prompt") or ""), language=None)
                if not event.get("system_prompt") and not event.get("prompt"):
                    st.caption("Görev ayrıntısı bu event içinde yok.")
        footer = card.empty()
        footer.caption(f"Adım: `{key}`")
        state = {
            "key": key,
            "agent": agent,
            "model": model,
            "header": header,
            "rslot": rslot,
            "aslot": aslot,
            "footer": footer,
            "reasoning": "",
            "content": "",
        }
        self.cards[key] = state
        self.latest_by_agent[agent] = key
        return state

    def handle(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        if kind == "agent_start":
            state = self._card(event)
            effort = event.get("reasoning_effort") or "provider-default"
            state["header"].markdown(
                f"⏳ **{event.get('agent')}** · `{event.get('model', '')}` — çalışıyor · reasoning `{effort}`"
            )
            return
        if kind == "agent_stream":
            state = self._card(event)
            delta = event.get("delta")
            if event.get("channel") == "reasoning" and isinstance(delta, str):
                state["reasoning"] += delta
                state["rslot"].markdown(state["reasoning"])
            elif event.get("channel") == "content" and isinstance(delta, str):
                state["content"] += delta
                state["aslot"].markdown(state["content"])
            return
        if kind == "llm_call":
            state = self._card(event)
            if event.get("provider_reasoning"):
                state["reasoning"] = str(event["provider_reasoning"])
                state["rslot"].markdown(state["reasoning"])
            if event.get("output"):
                state["content"] = str(event["output"])
                state["aslot"].markdown(state["content"])
            reasoning_tokens = int(event.get("reasoning_tokens", 0) or 0)
            if not state["reasoning"] and reasoning_tokens:
                state["rslot"].caption(
                    f"Sağlayıcı reasoning metnini göstermedi ({reasoning_tokens:,} token)."
                )
            cost = event.get("cost_usd")
            cost_label = f"${float(cost):.6f}" if cost is not None else "ücret N/A"
            state["header"].markdown(
                f"✅ **{event.get('agent')}** · `{event.get('model', '')}` — "
                f"{int(event.get('total_tokens', 0) or 0):,} token · {cost_label} · "
                f"{float(event.get('latency_s', 0) or 0):.1f} sn"
            )
            state["footer"].caption(
                f"input={int(event.get('prompt_tokens', 0) or 0):,} · "
                f"output={int(event.get('completion_tokens', 0) or 0):,} · "
                f"reasoning={reasoning_tokens:,} · cached={int(event.get('cached_tokens', 0) or 0):,} · "
                f"adım `{state['key']}`"
            )
            return
        if kind == "agent_error":
            state = self._card(event)
            state["header"].markdown(f"❌ **{event.get('agent')}** · `{event.get('model', '')}` — hata")
            state["footer"].error(str(event.get("error") or "Bilinmeyen hata"))
            return
        render_timeline_event(self.target, event)


def summary_metrics(summary: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Toplam çağrı", summary.get("total_calls", 0))
    c2.metric("Toplam token", f"{summary.get('total_tokens', 0):,}")
    c3.metric("Toplam ücret", cost_text(summary))
    c4.metric("Geçen süre", f"{float(summary.get('wall_time_s', 0)):.1f} sn")


def render_history(active: ProjectInfo) -> None:
    run_dirs = runs_for_project(RUNS_DIR, active.project_id, active.project_uuid)
    if not run_dirs:
        st.info("Bu projeye bağlı henüz kayıt yok.")
        return
    selected = st.selectbox("Deney kaydı", run_dirs, format_func=lambda p: p.name)
    summary_file = selected / "summary.json"
    if summary_file.exists():
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        summary_metrics(summary)
        rows = usage_rows(summary)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    events = load_run_events(selected, include_stream=True)
    st.subheader("Araştırma Timeline'ı")
    renderer = LiveTimelineRenderer(st.container(border=True))
    for event in events:
        renderer.handle(event)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _live_card_groups(events: list[dict], current_step: str) -> tuple[list[str], dict[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    latest_by_agent: dict[str, str] = {}
    allowed = {"agent_start", "agent_stream", "llm_call", "agent_error"}
    for event in events:
        if event.get("type") not in allowed:
            continue
        copy = dict(event)
        agent = str(copy.get("agent") or "Agent")
        step_key = str(copy.get("step_key") or "")
        if step_key:
            latest_by_agent[agent] = step_key
        else:
            step_key = latest_by_agent.get(agent, "")
            if step_key:
                copy["step_key"] = step_key
        if not step_key:
            continue
        if step_key not in groups:
            groups[step_key] = []
            order.append(step_key)
        groups[step_key].append(copy)
    ordered = list(reversed(order))
    if current_step in ordered:
        ordered.remove(current_step)
        ordered.insert(0, current_step)
    return ordered, groups


@st.fragment(run_every=1.0)
def render_live_run(active: ProjectInfo) -> None:
    project_root = PROJECTS.project_root(active.project_id)
    runtime = _read_json(project_root / "runtime.json", {})
    worker = _read_json(project_root / "worker.json", {})
    request = _read_json(project_root / "worker_request.json", {})
    status = str(runtime.get("status") or worker.get("status") or active.status or "READY")
    experiment_name = str(request.get("experiment_name") or active.experiment or "Deney")

    st.subheader(f"{experiment_name} · {active.project_id}")
    if runtime.get("last_error"):
        st.error(str(runtime["last_error"]))

    run_dirs = runs_for_project(RUNS_DIR, active.project_id, active.project_uuid)
    events: list[dict] = []
    latest: Path | None = None
    if run_dirs:
        latest = run_dirs[0]
        events = load_live_run_events(latest)
    elif status == "RUNNING":
        st.info("Worker başladı. İlk trace event'i bekleniyor…")

    snapshot = render_now_and_timeline(runtime, events, status=status)

    if events:
        st.markdown("#### Agent kartları")
        order, groups = _live_card_groups(events, str(snapshot.get("step_key") or ""))
        renderer = LiveTimelineRenderer(st.container(border=True))
        for step_key in order:
            for event in groups[step_key]:
                renderer.handle(event)

    c1, c2 = st.columns(2)
    if c1.button("DURDUR", type="primary", use_container_width=True, disabled=status != "RUNNING", key="live_stop"):
        (project_root / "stop.flag").write_text("stop requested\n", encoding="utf-8")
        st.warning("Durdurma isteği worker'a iletildi.")
    if c2.button("Research Control", use_container_width=True, key="live_control"):
        st.switch_page("pages/3_Research_Control.py")

    result_path = project_root / "worker_result.md"
    if result_path.exists() and status in {"COMPLETED", "STOPPED", "PAUSED_ERROR", "INTERRUPTED"}:
        if latest is not None and (latest / "summary.json").exists():
            summary_metrics(_read_json(latest / "summary.json", {}))
        with st.expander("Sonuç", expanded=status == "COMPLETED"):
            st.markdown(result_path.read_text(encoding="utf-8"))


def _worker_agent_payload(cfg: dict) -> dict:
    role = str(cfg["role"])
    return {
        "role": role,
        "display_role": str(cfg.get("display_role") or role),
        "system_prompt": str(cfg.get("prompt") or ""),
        "model": str(cfg.get("model") or ""),
        "temperature": float(cfg.get("temp", 0.2)),
        "max_tokens": None,
        "reasoning_effort": cfg.get("reasoning_effort"),
    }


def launch_experiment(
    exp_name: str,
    prompt: str,
    param,
    agents: list[dict],
    optional: dict[str, dict],
    extras: dict,
) -> int:
    exp = EXPERIMENTS[exp_name]
    project_id = extras["project_id"]
    method = str(exp["method"])
    agent_payload = [_worker_agent_payload(cfg) for cfg in agents]
    optional_payload = {name: _worker_agent_payload(cfg) for name, cfg in optional.items()}
    request = build_request_from_ui(
        project_id=project_id,
        experiment_method=method,
        experiment_name=exp_name,
        agents=agent_payload,
        optional_agents=optional_payload,
        prompt=prompt,
        param=int(param or 0),
        problem=prompt,
        iterations=int(param or 0),
        literature_query=extras.get("literature_query") or None,
        checkpoint_every=int(extras.get("checkpoint_every", 2)),
    )
    root = PROJECTS.project_root(project_id)
    write_worker_request(root, request)
    PROJECTS.touch(project_id, experiment=exp_name)
    return launch_worker(project_id)


def active_project_header(active: ProjectInfo) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        c1.markdown(f"### Aktif Proje · {active.title}")
        c1.caption(
            f"`{active.project_id}` · durum **{active.status}** · {active.run_count} run · ${active.total_cost_usd:.4f}"
        )
        if c2.button("Projeyi Değiştir", use_container_width=True):
            st.switch_page("pages/1_Projeler.py")
        if active.status in {"RUNNING", "PAUSED_ERROR", "STOPPED", "PAUSED", "STALE_RUNNING"}:
            if st.button("Araştırma Kontrolü", type="primary"):
                st.switch_page("pages/3_Research_Control.py")


def main() -> None:
    st.title("LLM Araştırma Laboratuvarı")
    active = PROJECTS.active_project()
    if active is None:
        st.info("Araştırma başlatmak için önce bir proje oluştur veya mevcut projeyi aç.")
        c1, c2 = st.columns(2)
        if c1.button("＋ Yeni Proje Oluştur", type="primary", use_container_width=True):
            st.session_state["show_create_project"] = True
            st.switch_page("pages/1_Projeler.py")
        if c2.button("Projeleri Aç", use_container_width=True):
            st.switch_page("pages/1_Projeler.py")
        st.stop()

    active_project_header(active)
    st.sidebar.header("Deney Ayarları")
    st.sidebar.caption(f"Aktif proje: **{active.title}** · `{active.project_id}`")
    if st.sidebar.button("Projeleri Aç", use_container_width=True):
        st.switch_page("pages/1_Projeler.py")

    api_ok = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    if not api_ok:
        st.sidebar.error("API anahtarı bulunamadı. `.env` içine OPENROUTER_API_KEY ekle.")
    model_ids, model_labels, catalog_error = model_catalog()
    if catalog_error:
        st.sidebar.warning("Canlı OpenRouter listesi alınamadı; fallback liste gösteriliyor.")
    if st.sidebar.button("Model listesini yenile"):
        load_openrouter_catalog.clear()
        st.rerun()

    names = list(EXPERIMENTS)
    default_exp = active.experiment if active.experiment in EXPERIMENTS else "Teorem Araştırması"
    exp_name = st.sidebar.selectbox("Deney tipi", names, index=names.index(default_exp))
    prompt, param, agents, optional, extras = build_sidebar(exp_name, model_ids, model_labels, active)

    tab_run, tab_hist = st.tabs(["Deney", "Proje Geçmişi"])
    with tab_run:
        st.info(
            "Tüm deney türleri ayrı worker process'te çalışır. Canlı akışı burada izleyebilir, "
            "sayfayı kapatıp daha sonra geri dönebilirsin."
        )
        running = active.status == "RUNNING"
        button_label = "Deney çalışıyor…" if running else "Deneyi Çalıştır"
        if st.button(
            button_label,
            type="primary",
            disabled=not api_ok or running,
            use_container_width=True,
        ):
            if not prompt.strip():
                st.warning("Lütfen bir problem/konu gir.")
            else:
                try:
                    pid = launch_experiment(exp_name, prompt, param, agents, optional, extras)
                    st.session_state["worker_pid"] = pid
                    st.session_state["live_run_project"] = active.project_id
                    st.success(f"Deney worker'ı başlatıldı · PID {pid}")
                    st.rerun()
                except Exception as exc:
                    st.exception(exc)

        show_live = active.status == "RUNNING" or st.session_state.get("live_run_project") == active.project_id
        if show_live:
            render_live_run(active)

    with tab_hist:
        render_history(active)


if __name__ == "__main__":
    main()
