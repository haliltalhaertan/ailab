from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lab import Agent, Orchestrator, Trace
from lab.openrouter_catalog import fetch_openrouter_models
from lab.project_manager import ProjectInfo, ProjectManager
from lab.prompts import ROLE_LIBRARY as THEOREM_ROLE_LIBRARY
from lab.reasoning_settings import get_reasoning_effort
from lab.ui_model import cost_text, event_summary, filter_models, load_run_events, runs_for_project, tool_status, usage_rows
from lab.worker_launcher import build_request_from_ui, launch_theorem_worker, write_worker_request

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
        "description": "Uzun theorem run'ı ayrı worker process'te çalışır. Tarayıcı kapanabilir; durdur/devam Research Control sayfasından yapılır.",
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
        param = st.sidebar.number_input(exp["param_label"], min_value=1, max_value=100 if exp["method"] == "theorem_lab" else 10, value=exp["param_default"])
    extras = {"project_id": active.project_id, "project_title": active.title}
    if exp["method"] == "theorem_lab":
        extras["literature_query"] = st.sidebar.text_input(
            "Literatür arama sorgusu",
            value=active.literature_query or os.environ.get("LAB_LITERATURE_QUERY", "tropical circuit reachability provenance lower bound"),
        )
        extras["checkpoint_every"] = st.sidebar.number_input("Checkpoint sıklığı (tur)", min_value=1, max_value=20, value=2)

    agents, optional = [], {}
    role_counts: dict[str, int] = {}
    for i, role in enumerate(exp["roles"]):
        role_counts[role] = role_counts.get(role, 0) + 1
        display_role = role if role_counts[role] == 1 else f"{role} {role_counts[role]}"
        key = f"{exp_name}_{i}_{role_counts[role]}"
        is_optional = role in exp["optional_roles"]
        if is_optional and not st.sidebar.checkbox(f"{display_role} dahil et", value=True, key=f"inc_{key}"):
            continue
        with st.sidebar.expander(display_role, expanded=exp["method"] == "theorem_lab"):
            sys_prompt = st.text_area("Sistem promptu", ROLE_LIBRARY.get(role, ROLE_LIBRARY["Panelist"]), key=f"p_{key}", height=110)
            wanted_default = default_model(role)
            query = st.text_input("Model ara", placeholder="örn. 5.3, glm, kimi, flash", key=f"search_{key}")
            choices = filter_models(model_ids, model_labels, query)
            if not query and wanted_default not in choices:
                choices.insert(0, wanted_default)
                model_labels.setdefault(wanted_default, wanted_default)
            if query:
                st.caption(f"{len(choices)} model eşleşti")
            if choices:
                preferred = wanted_default if wanted_default in choices else choices[0]
                model = st.selectbox("OpenRouter modeli", choices, index=choices.index(preferred), format_func=lambda mid: model_labels.get(mid, mid), key=f"m_{key}_{query.casefold()}")
            else:
                st.warning("Eşleşen model yok. Manuel model ID girebilirsin.")
                model = wanted_default
            manual = st.text_input("Manuel model ID (opsiyonel)", key=f"manual_{key}").strip()
            if manual:
                model = manual
            temp = st.slider("Sıcaklık", 0.0, 1.5, ROLE_TEMPS.get(role, 0.7), 0.05, key=f"t_{key}")
            st.caption(f"Kullanılacak model: `{model}`")
        cfg = {"role": role, "display_role": display_role, "prompt": sys_prompt, "model": model, "temp": temp}
        (optional if is_optional else agents if False else agents)
        if is_optional:
            optional[display_role] = cfg
        else:
            agents.append(cfg)
    return prompt, param, agents, optional, extras


def _agent(cfg: dict) -> Agent:
    return Agent(name=cfg["display_role"], system_prompt=cfg["prompt"], model=cfg["model"], temperature=cfg["temp"])


def _event_time(event: dict) -> str:
    raw = str(event.get("ts") or "")
    return raw.split("T", 1)[1][:8] if "T" in raw else ""


def render_timeline_event(target, event: dict) -> None:
    kind = str(event.get("type", "event"))
    if kind in {"agent_stream", "runtime_state", "agent_start", "llm_call", "agent_error", "agent_retry"}:
        return
    stamp = f"`{_event_time(event)}` " if _event_time(event) else ""
    if kind == "iteration_start":
        target.markdown(f"{stamp}**Tur {event.get('iteration')}** — başladı · {event.get('next_task', '')}")
    elif kind == "iteration_end":
        target.markdown(f"{stamp}**Tur {event.get('iteration')}** — `{event.get('item_id', '')}` · **{event.get('status', '')}**")
    elif kind == "status_downgraded_by_guard":
        target.warning(f"Evidence guard: `{event.get('requested')}` → `{event.get('granted')}` · {event.get('reason', '')}")
    elif kind == "literature_search_inconclusive":
        target.warning("Literatür taraması sonuçsuz kaldı; novelty hakkında sonuç çıkarılmadı.")
    elif kind == "literature_search":
        target.markdown(f"{stamp}**Literatür** — {len(event.get('results', []))} aday kayıt")
    elif kind == "tool_result":
        label, _ = tool_status(event)
        target.markdown(f"{stamp}**Araç** · `{event.get('tool', '')}` — **{label}**")
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
    REASONING_HEIGHT = 360
    ANSWER_HEIGHT = 360

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
        card = self.target.container(border=True)
        header = card.empty()
        header.markdown(f"⏳ **{agent}** · `{event.get('model', '')}`")
        rtab, atab = card.tabs(["🧠 Reasoning", "✍️ Cevap"])
        with rtab:
            with st.container(height=self.REASONING_HEIGHT, border=False):
                rslot = st.empty()
        with atab:
            with st.container(height=self.ANSWER_HEIGHT, border=False):
                aslot = st.empty()
        state = {"key": key, "agent": agent, "header": header, "rslot": rslot, "aslot": aslot, "reasoning": "", "content": ""}
        self.cards[key] = state
        self.latest_by_agent[agent] = key
        return state

    def handle(self, event: dict) -> None:
        kind = str(event.get("type") or "")
        if kind == "agent_start":
            state = self._card(event)
            state["header"].markdown(f"⏳ **{event.get('agent')}** · `{event.get('model', '')}` — çalışıyor")
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
            state["header"].markdown(f"✅ **{event.get('agent')}** · `{event.get('model', '')}` · {int(event.get('total_tokens', 0) or 0):,} token")
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


def execute_inline(exp_name: str, prompt: str, param, agents: list[dict], optional: dict[str, dict], extras: dict) -> dict | None:
    exp = EXPERIMENTS[exp_name]
    project_id = extras["project_id"]
    PROJECTS.touch(project_id, experiment=exp_name, status="RUNNING")
    trace = Trace(exp["slug"])
    info = PROJECTS.get(project_id)
    trace.log("project_context", project_id=project_id, project_uuid=info.project_uuid, title=info.title, experiment=exp_name)
    try:
        a_objs = [_agent(c) for c in agents]
        o_objs = {r: _agent(c) for r, c in optional.items()}
        orch = Orchestrator(trace)
        method = exp["method"]
        if method == "research_loop":
            result = orch.research_loop(prompt, a_objs[0], a_objs[1], iterations=int(param), synthesizer=o_objs.get("Raporcu"))
        elif method == "debate":
            result = orch.debate(prompt, a_objs[:2], rounds=int(param), judge=o_objs.get("Hakem"))
        elif method == "pipeline":
            result = orch.pipeline(prompt, a_objs)
        else:
            result = orch.panel(prompt, a_objs, synthesizer=o_objs.get("Sentezleyici"))
        PROJECTS.touch(project_id, status="COMPLETED")
        return {"result": result, "run_dir": str(trace.run_dir), "project_id": project_id, "exp": exp_name}
    except Exception:
        PROJECTS.touch(project_id, status="PAUSED_ERROR")
        raise
    finally:
        trace.close()


def launch_theorem(prompt: str, param: int, agents: list[dict], extras: dict) -> int:
    project_id = extras["project_id"]
    role_configs: dict[str, dict] = {}
    for cfg in agents:
        role = str(cfg["role"])
        role_configs[role] = {
            "name": role,
            "system_prompt": cfg["prompt"],
            "model": cfg["model"],
            "temperature": cfg["temp"],
            "max_tokens": None,
            "reasoning_effort": get_reasoning_effort(role),
        }
    request = build_request_from_ui(
        project_id=project_id,
        problem=prompt,
        iterations=int(param),
        literature_query=extras.get("literature_query") or None,
        checkpoint_every=int(extras.get("checkpoint_every", 2)),
        agents=role_configs,
    )
    root = PROJECTS.project_root(project_id)
    write_worker_request(root, request)
    PROJECTS.touch(project_id, experiment="Teorem Araştırması", status="RUNNING")
    return launch_theorem_worker(project_id)


def active_project_header(active: ProjectInfo) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        c1.markdown(f"### Aktif Proje · {active.title}")
        c1.caption(f"`{active.project_id}` · durum **{active.status}** · {active.run_count} run · ${active.total_cost_usd:.4f}")
        if c2.button("Projeyi Değiştir", use_container_width=True):
            st.switch_page("pages/1_Projeler.py")
        if active.status in {"RUNNING", "PAUSED_ERROR", "STOPPED", "PAUSED"}:
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
        if exp_name == "Teorem Araştırması":
            st.info("Teorem araştırması ayrı worker process'te çalışır. Bu sayfayı veya tarayıcıyı kapatabilirsin.")
        if st.button("Deneyi Çalıştır", type="primary", disabled=not api_ok, use_container_width=True):
            if not prompt.strip():
                st.warning("Lütfen bir problem/konu gir.")
            elif exp_name == "Teorem Araştırması":
                try:
                    pid = launch_theorem(prompt, int(param), agents, extras)
                    st.session_state["worker_pid"] = pid
                    st.switch_page("pages/3_Research_Control.py")
                except Exception as exc:
                    PROJECTS.touch(active.project_id, status="PAUSED_ERROR")
                    st.exception(exc)
            else:
                try:
                    data = execute_inline(exp_name, prompt, param, agents, optional, extras)
                except Exception as exc:
                    st.exception(exc)
                else:
                    if data:
                        st.success("Deney tamamlandı.")
                        st.markdown(data["result"])
    with tab_hist:
        render_history(active)


if __name__ == "__main__":
    main()
