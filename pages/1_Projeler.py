from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lab.agent import Agent
from lab.integrity import project_lock_is_live
from lab.openrouter_catalog import fetch_openrouter_models
from lab.project_manager import ProjectManager
from lab.project_planner import PROJECT_PLANNER_SYSTEM_PROMPT, generate_project_draft
from lab.trace import Trace
from lab.ui_model import load_default_agent_profile, profile_model_ids
from lab.ui_project_settings import (
    delete_run_history,
    force_stop_worker,
    local_storage_summary,
    save_project_ui_settings,
)


load_dotenv()
st.set_page_config(page_title="Projeler", layout="wide")
PROJECT_ROOT = Path("research_state")
RUNS_ROOT = Path("runs")
pm = ProjectManager(PROJECT_ROOT, RUNS_ROOT)
DEFAULT_AGENT_PROFILE = load_default_agent_profile()
THEOREM_DEFAULTS = dict(DEFAULT_AGENT_PROFILE.get("agents") or {})
ORCHESTRATOR_DEFAULT = dict(DEFAULT_AGENT_PROFILE.get("orchestrator_default") or {})
FALLBACK_MODELS = profile_model_ids(DEFAULT_AGENT_PROFILE)
THEOREM_ROLES = [
    "ResearchManager",
    "Theorist",
    "AdversarialCritic",
    "VerificationEngineer",
    "LiteratureScout",
    "IndependentAuditor",
]
ROLE_LABELS = {
    "ResearchManager": "Baş Araştırmacı / Manager",
    "Theorist": "Teorisyen",
    "AdversarialCritic": "Sceptik / Critic",
    "VerificationEngineer": "Doğrulayıcı / Verifier",
    "LiteratureScout": "Literatür Araştırmacısı",
    "IndependentAuditor": "Bağımsız Denetçi",
}

PROJECT_FORM_KEYS = [
    "new_title",
    "new_project_id",
    "new_description",
    "new_experiment",
    "new_experiment_widget",
    "new_problem",
    "new_literature_query",
    "new_tags",
]
EXPERIMENTS = ["Teorem Araştırması", "Araştırma Döngüsü", "Tartışma", "Zincir", "Panel"]


def fmt_time(value: str) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


def status_icon(status: str) -> str:
    status = status.upper()
    if status == "RUNNING":
        return "🟢"
    if status in {"PAUSED_ERROR", "STOPPED", "PAUSED", "STALE_RUNNING", "INTERRUPTED"}:
        return "🟠"
    if status in {"COMPLETED", "DONE"}:
        return "✅"
    return "⚪"


def reset_new_project_state() -> None:
    for key in PROJECT_FORM_KEYS + ["project_prompt", "project_draft_usage", "project_draft_source"]:
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if str(key).startswith("new_agent_model_") or str(key).startswith("new_generic_model"):
            st.session_state.pop(key, None)


def apply_draft(draft) -> None:
    data = draft.as_dict()
    st.session_state["new_title"] = data["title"]
    st.session_state["new_project_id"] = data["project_id"]
    st.session_state["new_description"] = data["description"]
    st.session_state["new_experiment"] = data["experiment"]
    st.session_state["new_experiment_widget"] = data["experiment"]
    st.session_state["new_problem"] = data["problem"]
    st.session_state["new_literature_query"] = data["literature_query"]
    st.session_state["new_tags"] = ", ".join(data["tags"])


def start_manual_project(user_prompt: str) -> None:
    seed = user_prompt.strip()
    st.session_state["new_title"] = "Yeni Araştırma"
    st.session_state["new_project_id"] = ""
    st.session_state["new_description"] = ""
    st.session_state["new_experiment"] = "Teorem Araştırması"
    st.session_state["new_experiment_widget"] = "Teorem Araştırması"
    st.session_state["new_problem"] = seed
    st.session_state["new_literature_query"] = ""
    st.session_state["new_tags"] = ""
    st.session_state["project_draft_usage"] = {}
    st.session_state["project_draft_source"] = "manual"


def generate_with_llm(user_prompt: str, model: str) -> None:
    trace = Trace("project_planner")
    agent = Agent(
        name="ProjectPlanner",
        system_prompt=PROJECT_PLANNER_SYSTEM_PROMPT,
        model=model,
        temperature=0.2,
    )
    trace.log("project_planner_start", agent=agent.name, model=model, prompt=user_prompt)
    try:
        draft, response, messages = generate_project_draft(user_prompt, agent)
        trace.agent_call(agent.name, response.model, agent.temperature, messages, response)
        trace.log(
            "project_context",
            project_id=draft.project_id,
            title=draft.title,
            experiment=draft.experiment,
        )
        trace.log("project_draft", project_id=draft.project_id, draft=draft.as_dict())
        summary_path = trace.close()
    except Exception:
        if not trace.closed:
            trace.close()
        raise
    apply_draft(draft)
    st.session_state["project_draft_source"] = "llm"
    try:
        st.session_state["project_draft_usage"] = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        st.session_state["project_draft_usage"] = {}


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


def _model_select(label: str, role: str, model_ids: list[str], model_labels: dict[str, str]) -> str:
    raw = THEOREM_DEFAULTS.get(role) or ORCHESTRATOR_DEFAULT
    wanted = str(raw.get("model") or ORCHESTRATOR_DEFAULT.get("model") or "z-ai/glm-5.3-flash")
    choices = list(model_ids)
    if wanted not in choices:
        choices.insert(0, wanted)
        model_labels.setdefault(wanted, wanted)
    return st.selectbox(
        label,
        choices,
        index=choices.index(wanted),
        format_func=lambda mid: model_labels.get(mid, mid),
        key=f"new_agent_model_{role}",
    )


def _selected_agent_defaults(model_ids: list[str], model_labels: dict[str, str]) -> tuple[dict[str, dict], dict]:
    agents: dict[str, dict] = {}
    for role in THEOREM_ROLES:
        model = _model_select(ROLE_LABELS[role], role, model_ids, model_labels)
        raw = dict(THEOREM_DEFAULTS.get(role) or {})
        agents[role] = {
            "model": model,
            "reasoning_effort": raw.get("reasoning_effort") or "medium",
        }
    generic_wanted = str(ORCHESTRATOR_DEFAULT.get("model") or "z-ai/glm-5.3-flash")
    generic_choices = list(model_ids)
    if generic_wanted not in generic_choices:
        generic_choices.insert(0, generic_wanted)
        model_labels.setdefault(generic_wanted, generic_wanted)
    generic_model = st.selectbox(
        "Araştırma Döngüsü / Tartışma / Zincir / Panel için genel model",
        generic_choices,
        index=generic_choices.index(generic_wanted),
        format_func=lambda mid: model_labels.get(mid, mid),
        key="new_generic_model",
    )
    generic = {
        "model": generic_model,
        "reasoning_effort": ORCHESTRATOR_DEFAULT.get("reasoning_effort") or "medium",
    }
    return agents, generic


st.title("Projeler")
st.caption("Önce projeyi ve araştırma ekibini kur; sonra deneyi başlat. Tüm kayıtlar bu bilgisayarda tutulur.")

active_id = pm.active_project_id()
if active_id:
    active = pm.active_project()
    if active:
        st.success(f"Aktif proje: **{active.title}** · `{active.project_id}` · {active.status}")

if st.button("＋ Yeni Proje Oluştur", type="primary"):
    reset_new_project_state()
    st.session_state["show_create_project"] = True
    st.rerun()

if st.session_state.get("show_create_project", False):
    with st.container(border=True):
        st.subheader("Yeni Proje")
        st.markdown("**1 · Ne araştırmak istediğini yaz. İstersen LLM taslağı hazırlasın, istersen doğrudan elle kur.**")
        project_prompt = st.text_area(
            "Proje promptu / başlangıç problemi",
            key="project_prompt",
            height=160,
            placeholder=(
                "Örn. Complete graph üzerinde tropical reachability provenance circuit lower bound problemini "
                "LLM + exact computation ile araştırmak istiyorum."
            ),
        )
        api_ok = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        with st.expander("ProjectPlanner ayarı", expanded=False):
            planner_model = st.text_input(
                "Planner modeli",
                value=os.environ.get("LAB_PROJECT_PLANNER_MODEL", "openai/gpt-4o-mini"),
                help="Bu model yalnız yeni proje taslağını doldurur; araştırmayı yapacak modelleri aşağıda ayrıca seçersin.",
            ).strip()
            st.caption("ProjectPlanner araştırma ekibinin modeli değildir.")
        generate_disabled = not project_prompt.strip() or not api_ok or not planner_model
        if not api_ok:
            st.info("API anahtarı yoksa da 'LLM kullanmadan elle kur' ile proje oluşturabilirsin.")
        setup_left, setup_right = st.columns(2)
        if setup_left.button(
            "✨ LLM ile Taslak Hazırla",
            type="primary",
            width="stretch",
            disabled=generate_disabled,
        ):
            with st.spinner("ProjectPlanner proje taslağını hazırlıyor..."):
                try:
                    generate_with_llm(project_prompt, planner_model)
                except Exception as exc:
                    st.error(f"ProjectPlanner başarısız: {exc}")
                else:
                    st.rerun()
        if setup_right.button("LLM kullanmadan elle kur", width="stretch"):
            start_manual_project(project_prompt)
            st.rerun()

        if all(key in st.session_state for key in ("new_title", "new_project_id", "new_problem")):
            source = str(st.session_state.get("project_draft_source") or "manual")
            usage = st.session_state.get("project_draft_usage") or {}
            if source == "llm":
                cost = usage.get("total_cost_usd")
                token_count = int(usage.get("total_tokens", 0) or 0)
                wall = float(usage.get("wall_time_s", 0) or 0)
                metrics = []
                if token_count:
                    metrics.append(f"{token_count:,} token")
                if cost is not None:
                    metrics.append(f"${float(cost):.6f}")
                if wall:
                    metrics.append(f"{wall:.1f} sn")
                st.success("ProjectPlanner taslağı hazırladı" + (" · " + " · ".join(metrics) if metrics else ""))
            else:
                st.info("Elle kurulum: proje alanlarını ve araştırma modellerini kendin seçiyorsun.")

            model_ids, model_labels, model_error = model_catalog()
            if model_error:
                st.warning(f"Canlı OpenRouter listesi alınamadı; production varsayılanları gösteriliyor. {model_error}")

            with st.expander("Proje ve araştırma ekibi", expanded=True):
                with st.form("create_project_form", clear_on_submit=False):
                    st.markdown("### 2 · Projeyi kontrol et")
                    title = st.text_input("Proje adı", key="new_title")
                    project_id = st.text_input("Project ID", key="new_project_id")
                    description = st.text_area("Kısa açıklama", key="new_description", height=90)
                    current_exp = st.session_state.get("new_experiment", "Teorem Araştırması")
                    if current_exp not in EXPERIMENTS:
                        current_exp = "Teorem Araştırması"
                        st.session_state["new_experiment"] = current_exp
                        st.session_state["new_experiment_widget"] = current_exp
                    experiment = st.selectbox(
                        "Varsayılan deney türü",
                        EXPERIMENTS,
                        index=EXPERIMENTS.index(current_exp),
                        key="new_experiment_widget",
                    )
                    problem = st.text_area("Başlangıç problemi", key="new_problem", height=220)
                    literature_query = st.text_input("Literatür arama sorgusu", key="new_literature_query")
                    tags_raw = st.text_input("Etiketler", key="new_tags")

                    st.markdown("### 3 · Araştırmayı yapacak LLM'leri seç")
                    st.caption(
                        "Bunlar proje oluşturulurken kaydedilir ve ilk deney ekranında varsayılan olarak gelir. "
                        "Daha sonra istediğin zaman değiştirebilirsin."
                    )
                    selected_agents, selected_generic = _selected_agent_defaults(model_ids, model_labels)

                    st.markdown("### 4 · Yerel kayıt")
                    st.info(
                        "Proje oluşturulduğu andan itibaren state/checkpoint/sonuçlar `research_state/<project_id>/`, "
                        "her run'ın trace/stream/summary dosyaları `runs/<run_id>/` altında bu bilgisayara kaydedilir."
                    )
                    st.caption(
                        "Kaydedilen başlıca dosyalar: `worker_result.md`, `state.json`, `checkpoints/`, `trace.jsonl`, "
                        "`stream.jsonl`, `summary.json`. PDF/DOCX otomatik oluşturulmaz. LLM çağrılarındaki prompt/yanıtlar "
                        "seçtiğin API sağlayıcısı üzerinden işlenir; proje klasörleri GitHub/Drive'a otomatik senkronlanmaz."
                    )
                    c1, c2 = st.columns(2)
                    create = c1.form_submit_button("Projeyi Oluştur ve Aç", type="primary", width="stretch")
                    cancel = c2.form_submit_button("İptal", width="stretch")
                if cancel:
                    reset_new_project_state()
                    st.session_state["show_create_project"] = False
                    st.rerun()
                if create:
                    try:
                        info = pm.create_project(
                            title=title,
                            project_id=project_id or None,
                            problem=problem,
                            description=description,
                            experiment=experiment,
                            literature_query=literature_query,
                            tags=[x.strip() for x in tags_raw.split(",") if x.strip()],
                            activate=True,
                        )
                        save_project_ui_settings(
                            pm.project_root(info.project_id),
                            agents=selected_agents,
                            orchestrator_default=selected_generic,
                        )
                    except Exception as exc:
                        st.error(str(exc))
                    else:
                        reset_new_project_state()
                        st.session_state["show_create_project"] = False
                        st.success(f"{info.title} oluşturuldu; araştırma ekibi kaydedildi.")
                        st.switch_page("app.py")
        else:
            if st.button("İptal", width="stretch"):
                reset_new_project_state()
                st.session_state["show_create_project"] = False
                st.rerun()

st.divider()
search = st.text_input("Projelerde ara", placeholder="proje adı, id, problem, etiket...")
show_archived = st.checkbox("Arşivlenmiş projeleri göster", value=False)
projects = pm.list_projects(include_archived=show_archived)
needle = search.strip().casefold()
if needle:
    projects = [
        p
        for p in projects
        if needle
        in " ".join([p.project_id, p.title, p.description, p.problem, " ".join(p.tags or [])]).casefold()
    ]

if not projects:
    st.info("Henüz proje yok. Yukarıdaki Yeni Proje Oluştur düğmesiyle başlayabilirsin.")

for project in projects:
    root = pm.project_root(project.project_id)
    storage = local_storage_summary(root, pm.runs_dir)
    with st.container(border=True):
        left, right = st.columns([4, 1])
        left.markdown(f"### {status_icon(project.status)} {project.title}")
        left.caption(f"`{project.project_id}` · son güncelleme {fmt_time(project.updated_at)}")
        left.caption(f"💾 Yerel kayıt: `{storage['project_root']}`")
        right.markdown(f"**{project.status}**")
        if project.description:
            st.write(project.description)
        if project.problem:
            st.caption(project.problem[:300] + ("…" if len(project.problem) > 300 else ""))

        c1, c2, c3, c4 = st.columns(4)
        counts = project.counts or {}
        c1.metric("Run", project.run_count)
        c2.metric("Token", f"{project.total_tokens:,}")
        c3.metric("Maliyet", f"${project.total_cost_usd:.4f}")
        c4.metric("OPEN / FAIL / PROVEN", f"{counts.get('OPEN',0)} / {counts.get('FAIL',0)} / {counts.get('PROVEN',0)}")

        b1, b2, b3, b4, b5 = st.columns(5)
        if b1.button("Aç", key=f"open_{project.project_id}", width="stretch"):
            pm.set_active(project.project_id)
            st.switch_page("app.py")
        if b2.button("Devam Et", key=f"resume_{project.project_id}", width="stretch"):
            pm.set_active(project.project_id)
            if project.status in {"PAUSED_ERROR", "STOPPED", "PAUSED", "INTERRUPTED", "STALE_RUNNING"}:
                st.switch_page("pages/3_Research_Control.py")
            else:
                st.switch_page("app.py")
        if b3.button("Kopyala", key=f"clone_toggle_{project.project_id}", width="stretch"):
            st.session_state[f"clone_{project.project_id}"] = True
        archive_label = "Arşivden Çıkar" if project.archived else "Arşivle"
        if b4.button(archive_label, key=f"archive_{project.project_id}", width="stretch"):
            pm.archive(project.project_id, not project.archived)
            st.rerun()
        if b5.button("Sil", key=f"delete_toggle_{project.project_id}", width="stretch"):
            st.session_state[f"delete_{project.project_id}"] = not st.session_state.get(f"delete_{project.project_id}", False)

        if st.session_state.get(f"clone_{project.project_id}"):
            with st.form(f"clone_form_{project.project_id}"):
                clone_title = st.text_input("Yeni proje adı", value=f"{project.title} Kopya")
                clone_id = st.text_input("Yeni project ID", value="")
                cc1, cc2 = st.columns(2)
                do_clone = cc1.form_submit_button("Kopyayı Oluştur", type="primary", width="stretch")
                close_clone = cc2.form_submit_button("Vazgeç", width="stretch")
            if close_clone:
                st.session_state[f"clone_{project.project_id}"] = False
                st.rerun()
            if do_clone:
                try:
                    new_project = pm.clone(project.project_id, title=clone_title, new_project_id=clone_id or None)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.session_state[f"clone_{project.project_id}"] = False
                    st.success(f"Kopya oluşturuldu: {new_project.project_id}")
                    st.rerun()

        if st.session_state.get(f"delete_{project.project_id}"):
            st.error("Yerel silme işlemi")
            if project_lock_is_live(root):
                st.warning(
                    "Bu projenin worker'ı şu anda çalışıyor. Aşağıdaki silme düğmelerinden biri seçilirse worker önce ZORLA DURDURULACAK."
                )
            st.caption(f"Proje state: `{storage['project_root']}`")
            st.caption(f"Run geçmişi: `{storage['runs_root']}`")
            confirm = st.text_input(f"Silmek için `{project.project_id}` yaz", key=f"delete_confirm_{project.project_id}")
            d1, d2, d3 = st.columns(3)
            if d1.button(
                "Sadece proje state'ini sil",
                key=f"delete_state_{project.project_id}",
                disabled=confirm != project.project_id,
                width="stretch",
            ):
                can_delete = True
                if project_lock_is_live(root):
                    can_delete = force_stop_worker(root)
                if not can_delete:
                    st.error("Worker zorla durdurulamadı; proje dosyaları silinmedi.")
                else:
                    pm.delete(project.project_id)
                    st.rerun()
            if d2.button(
                "HER ŞEYİ SİL · run logları dahil",
                key=f"delete_all_{project.project_id}",
                disabled=confirm != project.project_id,
                width="stretch",
                type="primary",
            ):
                can_delete = True
                if project_lock_is_live(root):
                    can_delete = force_stop_worker(root)
                if not can_delete:
                    st.error("Worker zorla durdurulamadı; proje ve run geçmişi silinmedi.")
                else:
                    summaries = pm.run_summaries(project.project_id)
                    deleted_runs = delete_run_history(summaries, pm.runs_dir)
                    pm.delete(project.project_id)
                    st.session_state[f"deleted_runs_{project.project_id}"] = deleted_runs
                    st.rerun()
            if d3.button("Vazgeç", key=f"delete_cancel_{project.project_id}", width="stretch"):
                st.session_state[f"delete_{project.project_id}"] = False
                st.rerun()

        with st.expander("Geçmiş / ayrıntılar", expanded=False):
            st.json(
                {
                    "project_id": project.project_id,
                    "project_uuid": project.project_uuid,
                    "status": project.status,
                    "experiment": project.experiment,
                    "tags": project.tags,
                    "runtime": project.runtime,
                    "counts": project.counts,
                    "local_project_root": storage["project_root"],
                    "local_runs_root": storage["runs_root"],
                }
            )
            runs = pm.run_summaries(project.project_id)[:20]
            if runs:
                st.dataframe(pd.DataFrame(runs), width="stretch", hide_index=True)
            else:
                st.caption("Bu projeye bağlı kayıtlı run bulunamadı.")
