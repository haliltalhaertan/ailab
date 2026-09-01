from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from lab.project_manager import ProjectManager


st.set_page_config(page_title="Projeler", layout="wide")
pm = ProjectManager()


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


st.title("Projeler")
st.caption("Araştırmaları run değil proje olarak yönet: oluştur, aç, durdurulan projeye devam et, geçmişini incele.")

active_id = pm.active_project_id()
if active_id:
    active = pm.active_project()
    if active:
        st.success(f"Aktif proje: **{active.title}** · `{active.project_id}` · {active.status}")

if st.button("＋ Yeni Proje Oluştur", type="primary"):
    st.session_state["show_create_project"] = True

if st.session_state.get("show_create_project", False):
    with st.container(border=True):
        st.subheader("Yeni Proje")
        with st.form("create_project_form", clear_on_submit=False):
            title = st.text_input("Proje adı", placeholder="Örn. Tropical Circuit Research")
            project_id = st.text_input("Project ID (opsiyonel)", placeholder="Boş bırakırsan proje adından üretilir")
            description = st.text_area("Kısa açıklama", height=90)
            experiment = st.selectbox(
                "Varsayılan deney türü",
                ["Teorem Araştırması", "Araştırma Döngüsü", "Tartışma", "Zincir", "Panel"],
                index=0,
            )
            problem = st.text_area("Başlangıç problemi", height=180, placeholder="Araştırma problemini burada tanımla...")
            literature_query = st.text_input("Literatür arama sorgusu (opsiyonel)")
            tags_raw = st.text_input("Etiketler", placeholder="math, tropical, circuits")
            c1, c2 = st.columns(2)
            create = c1.form_submit_button("Projeyi Oluştur ve Aç", type="primary", use_container_width=True)
            cancel = c2.form_submit_button("İptal", use_container_width=True)
        if cancel:
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
                st.session_state["show_create_project"] = False
                st.success(f"{info.title} oluşturuldu.")
                st.switch_page("app.py")

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
