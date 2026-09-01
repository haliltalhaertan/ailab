from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from lab.project_manager import ProjectManager
from lab.step_store import StepStore
from lab.ui_model import load_run_events, runs_for_project
from lab.worker_launcher import launch_theorem_worker, write_worker_request

ROOT = Path("research_state")
RUNS = Path("runs")
pm = ProjectManager(ROOT)
st.set_page_config(page_title="Araştırma Kontrolü", layout="wide")
st.title("Araştırma Kontrolü")
st.caption("Teorem araştırması Streamlit'ten bağımsız worker process'te çalışır. Bu sayfadan izle, durdur veya devam ettir.")


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


projects = pm.list_projects(include_archived=False)
if not projects:
    st.info("Henüz proje yok.")
    if st.button("Proje Oluştur"):
        st.switch_page("pages/1_Projeler.py")
    st.stop()

ids = [p.project_id for p in projects]
active_id = pm.active_project_id()
index = ids.index(active_id) if active_id in ids else 0
selected_id = st.selectbox("Proje", ids, index=index, format_func=lambda pid: next((p.title for p in projects if p.project_id == pid), pid))
if selected_id != active_id:
    pm.set_active(selected_id)
project_info = pm.get(selected_id)
project = ROOT / selected_id
runtime_path = project / "runtime.json"
config_path = project / "run_config.json"
worker_path = project / "worker.json"
request_path = project / "worker_request.json"
stop_path = project / "stop.flag"
store = StepStore(project)


@st.fragment(run_every=1.0)
def live_status() -> None:
    runtime = read_json(runtime_path, {})
    worker = read_json(worker_path, {})
    counts = store.counts()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Durum", runtime.get("status", worker.get("status", "READY")))
    c2.metric("Tamamlanan tur", runtime.get("completed_iterations", 0))
    c3.metric("Şu anki tur", runtime.get("current_iteration", 0))
    c4.metric("Tamamlanan adım", counts["complete_steps"])
    c5.metric("Partial", counts["partials"])
    st.write("**Şu anki adım:**", runtime.get("current_step", "-"))
    if worker.get("pid"):
        st.caption(f"Worker PID: `{worker.get('pid')}` · run `{worker.get('run_id', '-')}`")
    if runtime.get("next_task"):
        st.write("**Sonraki araştırma hedefi:**", runtime["next_task"])
    if runtime.get("last_error"):
        st.error(runtime["last_error"])

    run_dirs = runs_for_project(RUNS, selected_id, project_info.project_uuid)
    if run_dirs:
        latest = run_dirs[0]
        events = load_run_events(latest, include_stream=False)[-15:]
        if events:
            with st.expander("Son olaylar", expanded=True):
                for event in events:
                    kind = event.get("type", "event")
                    if kind == "iteration_end":
                        st.write(f"Tur {event.get('iteration')}: {event.get('item_id')} → {event.get('status')}")
                    elif kind == "status_downgraded_by_guard":
                        st.warning(f"Guard {event.get('requested')} → {event.get('granted')}: {event.get('reason')}")
                    elif kind in {"run_paused", "worker_error", "literature_search_inconclusive"}:
                        st.warning(str(event.get("error") or event.get("warning") or kind))
                    elif kind == "llm_call":
                        st.caption(f"{event.get('agent')} tamamlandı · {event.get('total_tokens', 0)} token")

    result_path = project / "worker_result.md"
    if result_path.exists() and str(runtime.get("status") or worker.get("status") or "") in {"COMPLETED", "STOPPED", "PAUSED_ERROR"}:
        with st.expander("Son worker sonucu", expanded=False):
            st.markdown(result_path.read_text(encoding="utf-8"))


live_status()

left, right = st.columns(2)
with left:
    if st.button("DURDUR", type="primary", use_container_width=True):
        stop_path.write_text("stop requested\n", encoding="utf-8")
        st.warning("Durdurma isteği yazıldı. Worker LLM/deney adımını mümkün olan ilk güvenli noktada durduracak.")
with right:
    if st.button("Durdurma isteğini iptal et", use_container_width=True):
        stop_path.unlink(missing_ok=True)
        st.success("Stop flag kaldırıldı.")

st.divider()
st.subheader("Kaldığı yerden devam et")
config = read_json(config_path, {})
if not config:
    st.warning("Bu proje için run_config.json yok. Ana sayfada ilk theorem run'ını başlat.")
else:
    st.caption(
        "Model değiştirirsen yalnız henüz tamamlanmamış adımlar yeni modeli kullanır. Tamamlanmış step cache'i model değişikliği yüzünden yeniden ücretlendirilmez. "
        "System prompt / temperature / reasoning effort değişirse ilgili step fingerprint'i bilinçli olarak değişir."
    )
    edited_agents: dict[str, dict] = {}
    changed_models: list[str] = []
    for role, raw in config.get("agents", {}).items():
        if role == "CodeExperimentAgent":
            # It may still be edited; keep it in the request.
            pass
        col1, col2 = st.columns([1, 2])
        col1.write(f"**{role}**")
        col1.caption(f"reasoning: `{raw.get('reasoning_effort') or 'provider-default'}`")
        value = col2.text_input(f"{role} model", value=str(raw.get("model") or ""), key=f"model_{selected_id}_{role}", label_visibility="collapsed")
        edited = dict(raw)
        edited["model"] = value
        edited_agents[role] = edited
        if value != str(raw.get("model") or ""):
            changed_models.append(role)
    if changed_models:
        st.info("Model override: " + ", ".join(changed_models) + ". Tamamlanmış adımlar korunacak; override yalnız incomplete/partial adımlarda etkili.")

    runtime = read_json(runtime_path, {})
    running = str(runtime.get("status") or "") == "RUNNING"
    if st.button("ŞİMDİ DEVAM ET", type="primary", use_container_width=True, disabled=running):
        stop_path.unlink(missing_ok=True)
        frozen = read_json(project / "problem_frozen.json", {})
        request = {
            "request_version": 1,
            "project_id": selected_id,
            "project_uuid": project_info.project_uuid,
            "problem": str(config.get("problem") or frozen.get("problem") or ""),
            "iterations": int(config.get("iterations", 5)),
            "literature_query": config.get("literature_query"),
            "checkpoint_every": int(config.get("checkpoint_every", 2)),
            "agents": edited_agents,
            "code_experiment": config.get("code_experiment") or {},
        }
        write_worker_request(project, request)
        try:
            pid = launch_theorem_worker(selected_id, root=ROOT)
        except Exception as exc:
            st.exception(exc)
        else:
            pm.touch(selected_id, status="RUNNING")
            st.success(f"Worker başlatıldı: PID {pid}")
            st.rerun()

with st.expander("Kaydedilmiş run config", expanded=False):
    st.json(config)
with st.expander("Runtime cursor", expanded=False):
    st.json(read_json(runtime_path, {}))
with st.expander("Partial/soft-resume adımları", expanded=False):
    st.json(store.list_partials())
with st.expander("Step cache", expanded=False):
    st.dataframe(store.list_steps(), use_container_width=True, hide_index=True)
with st.expander("Worker isteği", expanded=False):
    st.json(read_json(request_path, {}))
