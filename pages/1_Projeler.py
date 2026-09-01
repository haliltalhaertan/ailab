from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lab.agent import Agent
from lab.project_manager import ProjectManager
from lab.project_planner import PROJECT_PLANNER_SYSTEM_PROMPT, generate_project_draft
from lab.trace import Trace


load_dotenv()
st.set_page_config(page_title="Projeler", layout="wide")
pm = ProjectManager()

PROJECT_FORM_KEYS = [
    "new_title",
    "new_project_id",
    "new_description",
    "new_experiment",
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
    if status in {"PAUSED_ERROR", "STOPPED", "PAUSED"}:
        return "🟠"
    if status in {"COMPLETED", "DONE"}:
        return "✅"
    return "⚪"


def reset_new_project_state() -> None:
    for key in PROJECT_FORM_KEYS + ["project_prompt", "project_draft_usage"]:
        st.session_state.pop(key, None)


def apply_draft(draft) -> None:
    data = draft.as_dict()
    st.session_state["new_title"] = data["title"]
    st.session_state["new_project_id"] = data["project_id"]
    st.session_state["new_description"] = data["description"]
    st.session_state["new_experiment"] = data["experiment"]
    st.session_state["new_problem"] = data["problem"]
    st.session_state["new_literature_query"] = data["literature_query"]
    st.session_state["new_tags"] = ", ".join(data["tags"])


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
    try:
        st.session_state["project_draft_usage"] = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        st.session_state["project_draft_usage"] = {}


st.title("Projeler")
st.caption("Araştırmaları run değil proje olarak yönet: oluştur, aç, durdurulan projeye devam et, geçmişini incele.")

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
        st.markdown("**Sadece ne araştırmak istediğini yaz. ProjectPlanner geri kalan proje alanlarını hazırlasın.**")
        project_prompt = st.text_area(
            "Proje promptu",
            key="project_prompt",
            height=160,
            placeholder=(
                "Örn. Complete graph üzerinde tropical reachability provenance circuit lower bound problemini "
                "LLM + exact computation ile araştırmak istiyorum. Literatürde gerçekten açık olan dar bir theorem hedefi seç."
            ),
        )
        api_ok = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        with st.expander("ProjectPlanner ayarı", expanded=False):
            planner_model = st.text_input(
                "Planner modeli",
                value=os.environ.get("LAB_PROJECT_PLANNER_MODEL", "openai/gpt-4o-mini"),
                help="OpenRouter model slug. Reasoning seviyesi Reasoning Ayarları sayfasındaki ProjectPlanner satırından gelir.",
            ).strip()
            st.caption("Bu ayar opsiyoneldir; normal kullanımda sadece yukarıdaki proje promptunu yazman yeterli.")
        generate_disabled = not project_prompt.strip() or not api_ok or not planner_model
        if not api_ok:
            st.warning("ProjectPlanner için `.env` içinde OPENROUTER_API_KEY gerekli.")
        if st.button(
            "✨ LLM ile Projeyi Hazırla",
            type="primary",
            use_container_width=True,
            disabled=generate_disabled,
        ):
            with st.spinner("ProjectPlanner proje taslağını hazırlıyor..."):
                try:
                    generate_with_llm(project_prompt, planner_model)
                except Exception as exc:
                    st.error(f"ProjectPlanner başarısız: {exc}")
                else:
                    st.rerun()

        if all(key in st.session_state for key in ("new_title", "new_project_id", "new_problem")):
            usage = st.session_state.get("project_draft_usage") or {}
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

            with st.expander("LLM'nin doldurduğu proje alanları", expanded=True):
                with st.form("create_project_form", clear_on_submit=False):
                    title = st.text_input("Proje adı", key="new_title")
                    project_id = st.text_input("Project ID", key="new_project_id")
                    description = st.text_area("Kısa açıklama", key="new_description", height=90)
                    current_exp = st.session_state.get("new_experiment", "Teorem Araştırması")
                    if current_exp not in EXPERIMENTS:
                        current_exp = "Teorem Araştırması"
                        st.session_state["new_experiment"] = current_exp
                    experiment = st.selectbox(
                        "Varsayılan deney türü",
                        EXPERIMENTS,
                        index=EXPERIMENTS.index(current_exp),
                        key="new_experiment_widget",
                    )
                    problem = st.text_area("Başlangıç problemi", key="new_problem", height=220)
                    literature_query = st.text_input(
                        "Literatür arama sorgusu", key="new_literature_query"
                    )
                    tags_raw = st.text_input("Etiketler", key="new_tags")
                    c1, c2 = st.columns(2)
                    create = c1.form_submit_button(
                        "Projeyi Oluştur ve Aç", type="primary", use_container_width=True
                    )
                    cancel = c2.form_submit_button("İptal", use_container_width=True)
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
                    except Exception as exc:
                        st.error(str(exc))
                    else:
                        reset_new_project_state()
                        st.session_state["show_create_project"] = False
                        st.success(f"{info.title} oluşturuldu.")
                        st.switch_page("app.py")
        else:
            if st.button("İptal", use_container_width=True):
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
        p for p in projects
        if needle in " ".join([
            p.project_id,
            p.title,
            p.description,
            p.problem,
            " ".join(p.tags or []),
        ]).casefold()
    ]

if not projects:
    st.info("Henüz proje yok. Yukarıdaki Yeni Proje Oluştur düğmesiyle başlayabilirsin.")

for project in projects:
    with st.container(border=True):
        left, right = st.columns([4, 1])
        left.markdown(f"### {status_icon(project.status)} {project.title}")
        left.caption(f"`{project.project_id}` · son güncelleme {fmt_time(project.updated_at)}")
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

        b1, b2, b3, b4 = st.columns(4)
        if b1.button("Aç", key=f"open_{project.project_id}", use_container_width=True):
            pm.set_active(project.project_id)
            st.switch_page("app.py")
        if b2.button("Devam Et", key=f"resume_{project.project_id}", use_container_width=True):
            pm.set_active(project.project_id)
            if project.status in {"PAUSED_ERROR", "STOPPED", "PAUSED"}:
                st.switch_page("pages/3_Research_Control.py")
            else:
                st.switch_page("app.py")
        if b3.button("Kopyala", key=f"clone_toggle_{project.project_id}", use_container_width=True):
            st.session_state[f"clone_{project.project_id}"] = True
        archive_label = "Arşivden Çıkar" if project.archived else "Arşivle"
        if b4.button(archive_label, key=f"archive_{project.project_id}", use_container_width=True):
            pm.archive(project.project_id, not project.archived)
            st.rerun()

        if st.session_state.get(f"clone_{project.project_id}"):
            with st.form(f"clone_form_{project.project_id}"):
                clone_title = st.text_input("Yeni proje adı", value=f"{project.title} Kopya")
                clone_id = st.text_input("Yeni project ID", value="")
                cc1, cc2 = st.columns(2)
                do_clone = cc1.form_submit_button("Kopyayı Oluştur", type="primary", use_container_width=True)
                close_clone = cc2.form_submit_button("Vazgeç", use_container_width=True)
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

        with st.expander("Geçmiş / ayrıntılar", expanded=False):
            st.json({
                "project_id": project.project_id,
                "status": project.status,
                "experiment": project.experiment,
                "tags": project.tags,
                "runtime": project.runtime,
                "counts": project.counts,
            })
            runs = pm.run_summaries(project.project_id)[:20]
            if runs:
                st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)
            else:
                st.caption("Bu projeye bağlı kayıtlı run bulunamadı.")

        with st.expander("Tehlikeli işlemler", expanded=False):
            st.warning("Silme işlemi araştırma state klasörünü kalıcı olarak siler. Runs klasöründeki eski loglar silinmez.")
            confirm = st.text_input(
                f"Silmek için `{project.project_id}` yaz",
                key=f"delete_confirm_{project.project_id}",
            )
            if st.button(
                "Projeyi Kalıcı Sil",
                key=f"delete_{project.project_id}",
                disabled=confirm != project.project_id,
            ):
                pm.delete(project.project_id)
                st.rerun()
