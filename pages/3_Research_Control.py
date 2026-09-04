from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from lab.integrity import project_lock_is_live
from lab.openrouter_catalog import fetch_openrouter_models
from lab.project_manager import ProjectManager
from lab.runtime_health import cleanup_stale_run
from lab.step_store import StepStore
from lab.ui_live import render_now_and_timeline
from lab.ui_model import (
    filter_models,
    load_default_agent_profile,
    load_live_run_events,
    profile_model_ids,
    runs_for_project,
)
from lab.ui_project_settings import force_stop_worker, local_storage_summary
from lab.ui_tool_availability import tool_availability_caption, tool_availability_rows
from lab.worker_launcher import launch_worker, write_worker_request

ROOT = Path("research_state")
RUNS = Path("runs")
pm = ProjectManager(ROOT)
st.set_page_config(page_title="Araştırma Kontrolü", layout="wide")
st.title("Araştırma Kontrolü")
st.caption("Canlı worker'ı izle, normal durdur, gerekirse zorla sonlandır veya devam et.")

DEFAULT_AGENT_PROFILE = load_default_agent_profile()
FALLBACK_MODELS = profile_model_ids(DEFAULT_AGENT_PROFILE)


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


projects = pm.list_projects(include_archived=False)
if not projects:
    st.info("Henüz proje yok.")
    if st.button("Proje Oluştur"):
        st.switch_page("pages/1_Projeler.py")
    st.stop()

ids = [p.project_id for p in projects]
active_id = pm.active_project_id()
index = ids.index(active_id) if active_id in ids else 0
selected_id = st.selectbox(
    "Proje",
    ids,
    index=index,
    format_func=lambda pid: next((p.title for p in projects if p.project_id == pid), pid),
)
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
request = read_json(request_path, {})
experiment_method = str(request.get("experiment_method") or "theorem_lab")
experiment_name = str(request.get("experiment_name") or project_info.experiment or "Deney")
storage = local_storage_summary(project, RUNS)

st.caption(f"**{experiment_name}** · `{selected_id}` · method `{experiment_method}`")
st.success(f"💾 Yerel kayıt açık · `{storage['project_root']}`")
with st.expander("Yerel dosya konumları", expanded=False):
    st.markdown("**Proje state / checkpoint / son worker sonucu**")
    st.code(storage["project_root"], language=None)
    st.markdown("**Tüm run trace / stream / summary klasörleri**")
    st.code(storage["runs_root"], language=None)

if experiment_method == "theorem_lab":
    tool_snapshot = read_json(project / "tool_availability.json", {})
    tool_rows = tool_availability_rows(tool_snapshot)
    st.markdown("#### Araç yetenekleri")
    if tool_rows:
        columns = st.columns(len(tool_rows))
        for column, row in zip(columns, tool_rows):
            column.metric(str(row["label"]), "AÇIK" if row["available"] else "KAPALI")
            column.caption(str(row["reason"]))
    else:
        st.caption("Henüz capability snapshot yok.")
    st.caption(tool_availability_caption(tool_snapshot))


@st.fragment(run_every=1.0)
def live_status() -> None:
    current_info = pm.get(selected_id)
    runtime = dict(current_info.runtime or {})
    worker = read_json(worker_path, {})
    if worker.get("pid"):
        st.caption(f"Worker PID: `{worker.get('pid')}` · run `{worker.get('run_id', '-')}`")
    if runtime.get("last_error"):
        st.error(str(runtime["last_error"]))

    run_dirs = runs_for_project(RUNS, selected_id, current_info.project_uuid)
    events = load_live_run_events(run_dirs[0]) if run_dirs else []
    render_now_and_timeline(runtime, events, status=current_info.status)

    if experiment_method == "theorem_lab":
        counts = store.counts()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tamamlanan tur", runtime.get("completed_iterations", 0))
        c2.metric("Şu anki tur", runtime.get("current_iteration", 0))
        c3.metric("Tamamlanan adım", counts["complete_steps"])
        c4.metric("Partial", counts["partials"])
        if runtime.get("next_task"):
            st.write("**Sonraki araştırma hedefi:**", runtime["next_task"])

    result_path = project / "worker_result.md"
    if result_path.exists() and current_info.status in {"COMPLETED", "STOPPED", "PAUSED_ERROR", "INTERRUPTED"}:
        with st.expander("Son worker sonucu", expanded=False):
            st.markdown(result_path.read_text(encoding="utf-8"))


live_status()

current_info = pm.get(selected_id)
running = project_lock_is_live(project)
left, middle, right = st.columns(3)
with left:
    if st.button(
        "DURDUR",
        type="primary",
        width="stretch",
        disabled=not running or stop_path.exists(),
    ):
        stop_path.write_text("stop requested\n", encoding="utf-8")
        st.warning("Durdurma isteği yazıldı. Worker ilk güvenli kesme noktasında duracak.")
        st.rerun()
with middle:
    if st.button(
        "ZORLA DURDUR · HEMEN",
        width="stretch",
        disabled=not running,
        help="Worker process'ini hemen sonlandırır; son çağrının partial çıktısı eksik kalabilir.",
    ):
        if force_stop_worker(project):
            st.warning("Worker zorla sonlandırıldı. Kaydedilmiş proje dosyaları korunuyor.")
        else:
            st.info("Canlı worker process'i bulunamadı.")
        st.rerun()
with right:
    if st.button("Durdurma isteğini iptal et", width="stretch", disabled=not stop_path.exists()):
        stop_path.unlink(missing_ok=True)
        st.success("Stop flag kaldırıldı.")
        st.rerun()

current_info = pm.get(selected_id)
if current_info.status == "STALE_RUNNING" and not running:
    st.warning(
        "Stale worker kaydı bulundu. Temizlemek yalnız stale run.lock/runtime işaretini kaldırır; "
        "theorem step cache ve partial içerikler varsa korunur."
    )
    if st.button("Stale run'ı temizle", width="stretch"):
        try:
            cleanup_stale_run(project)
        except Exception as exc:
            st.exception(exc)
        else:
            st.success("Stale run temizlendi; durum INTERRUPTED.")
            st.rerun()

st.divider()
config = read_json(config_path, {})

if experiment_method != "theorem_lab":
    st.subheader("Yeniden çalıştır")
    st.caption(
        "Theorem dışı deneylerde step-level resume yoktur. Aynı worker isteğini yeni bir run olarak yeniden başlatabilirsin."
    )
    running = project_lock_is_live(project)
    if not request:
        st.warning("Yeniden çalıştırılacak worker_request.json bulunamadı.")
    elif st.button("YENİDEN ÇALIŞTIR", type="primary", width="stretch", disabled=running):
        stop_path.unlink(missing_ok=True)
        retry_request = dict(request)
        retry_request["project_id"] = selected_id
        retry_request["project_uuid"] = project_info.project_uuid
        write_worker_request(project, retry_request)
        try:
            pid = launch_worker(selected_id, root=ROOT)
        except Exception as exc:
            st.exception(exc)
        else:
            st.success(f"Worker başlatıldı: PID {pid}")
            st.rerun()
else:
    st.subheader("Kaldığı yerden devam et")
    if not config:
        st.warning("Bu proje için run_config.json yok. Ana sayfada ilk theorem run'ını başlat.")
    else:
        st.caption(
            "Model değiştirirsen yalnız henüz tamamlanmamış adımlar yeni modeli kullanır. Tamamlanmış step cache'i "
            "model değişikliği yüzünden yeniden ücretlendirilmez. System prompt / temperature / reasoning effort değişirse "
            "ilgili step fingerprint'i bilinçli olarak değişir."
        )
        model_ids, model_labels, model_error = model_catalog()
        if model_error:
            st.warning(f"OpenRouter katalog uyarısı: {model_error}")
        edited_agents: dict[str, dict] = {}
        changed_models: list[str] = []
        for role, raw in config.get("agents", {}).items():
            st.write(f"**{role}**")
            st.caption(f"reasoning: `{raw.get('reasoning_effort') or 'provider-default'}`")
            wanted_default = str(raw.get("model") or "")
            query = st.text_input(
                f"{role} model ara",
                placeholder="örn. glm, kimi, 5.3, flash",
                key=f"resume_search_{selected_id}_{role}",
            )
            choices = filter_models(model_ids, model_labels, query)
            if not query and wanted_default and wanted_default not in choices:
                choices.insert(0, wanted_default)
                model_labels.setdefault(wanted_default, wanted_default)
            if choices:
                preferred = wanted_default if wanted_default in choices else choices[0]
                selected_model = st.selectbox(
                    f"{role} OpenRouter modeli",
                    choices,
                    index=choices.index(preferred),
                    format_func=lambda mid: model_labels.get(mid, mid),
                    key=f"resume_model_{selected_id}_{role}_{query.casefold()}",
                )
            else:
                st.warning("Eşleşen model yok. Manuel model ID girebilirsin.")
                selected_model = wanted_default
            manual = st.text_input(
                f"{role} manuel model ID (opsiyonel)",
                key=f"resume_manual_{selected_id}_{role}",
            ).strip()
            value = manual or selected_model
            st.caption(f"Kullanılacak model: `{value}`")
            edited = dict(raw)
            edited["model"] = value
            edited_agents[role] = edited
            if value != wanted_default:
                changed_models.append(role)
        if changed_models:
            st.info(
                "Model override: " + ", ".join(changed_models) + ". Tamamlanmış adımlar korunacak; override yalnız incomplete/partial adımlarda etkili."
            )

        running = project_lock_is_live(project)
        if st.button("ŞİMDİ DEVAM ET", type="primary", width="stretch", disabled=running):
            stop_path.unlink(missing_ok=True)
            frozen = read_json(project / "problem_frozen.json", {})
            resume_request = {
                "request_version": 2,
                "project_id": selected_id,
                "project_uuid": project_info.project_uuid,
                "experiment_method": "theorem_lab",
                "experiment_name": "Teorem Araştırması",
                "problem": str(config.get("problem") or frozen.get("problem") or ""),
                "prompt": str(config.get("problem") or frozen.get("problem") or ""),
                "iterations": int(config.get("iterations", 5)),
                "param": int(config.get("iterations", 5)),
                "literature_query": config.get("literature_query"),
                "checkpoint_every": int(config.get("checkpoint_every", 2)),
                "agents": edited_agents,
                "optional_agents": {},
                "code_experiment": config.get("code_experiment") or {},
            }
            write_worker_request(project, resume_request)
            try:
                pid = launch_worker(selected_id, root=ROOT)
            except Exception as exc:
                st.exception(exc)
            else:
                st.success(f"Worker başlatıldı: PID {pid}")
                st.rerun()

if experiment_method == "theorem_lab":
    with st.expander("Kaydedilmiş run config", expanded=False):
        st.json(config)
    with st.expander("Partial/soft-resume adımları", expanded=False):
        st.json(store.list_partials())
    with st.expander("Step cache", expanded=False):
        st.dataframe(store.list_steps(), width="stretch", hide_index=True)
with st.expander("Runtime cursor", expanded=False):
    st.json(read_json(runtime_path, {}))
with st.expander("Worker isteği", expanded=False):
    st.json(read_json(request_path, {}))
