import json
from pathlib import Path

import streamlit as st

from lab import (
    Agent,
    ProjectBusyError,
    ResearchState,
    ResearchToolbox,
    TheoremResearchLab,
    Trace,
)
from lab.project_manager import ProjectManager


ROOT = Path("research_state")
pm = ProjectManager(ROOT)
st.set_page_config(page_title="Araştırma Kontrolü", layout="wide")
st.title("Araştırma Kontrolü")
st.caption("Aktif araştırmayı güvenli durdur veya ilk tamamlanmamış agent/tool adımından devam ettir.")


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
selected_id = st.selectbox(
    "Proje",
    ids,
    index=index,
    format_func=lambda pid: next((p.title for p in projects if p.project_id == pid), pid),
)
if selected_id != active_id:
    pm.set_active(selected_id)
project = ROOT / selected_id

runtime_path = project / "runtime.json"
config_path = project / "run_config.json"
stop_path = project / "stop.flag"
cache_path = project / "step_cache.json"
partial_path = project / "partial_steps.json"
runtime = read_json(runtime_path, {})
config = read_json(config_path, {})
cache = read_json(cache_path, {})
partials = read_json(partial_path, {})

c1, c2, c3, c4 = st.columns(4)
c1.metric("Durum", runtime.get("status", "READY"))
c2.metric("Tamamlanan tur", runtime.get("completed_iterations", 0))
c3.metric("Şu anki tur", runtime.get("current_iteration", 0))
c4.metric(
    "Tamamlanan adım",
    len([v for v in cache.values() if isinstance(v, dict) and v.get("status") == "COMPLETE"]),
)
st.write("**Şu anki adım:**", runtime.get("current_step", "-"))
if partials:
    st.info(f"Yarım kalmış/soft-resume için saklanan {len(partials)} LLM adımı var.")
if runtime.get("next_task"):
    st.write("**Sonraki araştırma hedefi:**", runtime["next_task"])
if runtime.get("last_error"):
    st.error(runtime["last_error"])

left, right = st.columns(2)
with left:
    if st.button("DURDUR", type="primary", use_container_width=True):
        stop_path.write_text("stop requested\n", encoding="utf-8")
        st.warning(
            "Durdurma isteği yazıldı. LLM çağrısı ve çalışan Python deneyi mümkün olan ilk noktada durdurulacak; "
            "tamamlanan işler ve provider-visible partial çalışma korunacak."
        )
with right:
    if st.button("Durdurma isteğini iptal et", use_container_width=True):
        stop_path.unlink(missing_ok=True)
        st.success("Stop flag kaldırıldı.")

st.divider()
st.subheader("Kaldığı yerden devam et")
if not config:
    st.warning("Bu proje için run_config.json yok. Ana sayfada projeyi açıp ilk theorem run'ını başlat.")
    if st.button("Projeyi Ana Sayfada Aç"):
        pm.set_active(selected_id)
        st.switch_page("app.py")
else:
    st.caption(
        "Model slug'ını bilinçli olarak değiştirebilirsin. Reasoning effort, temperature, system prompt "
        "ve code-experiment limitleri kaydedilmiş run config'ten aynen geri yüklenir."
    )
    edited = {}
    for role, raw in config.get("agents", {}).items():
        col1, col2 = st.columns([1, 2])
        effort = raw.get("reasoning_effort")
        effort_label = effort if effort is not None else "provider-default"
        col1.write(f"**{role}**")
        col1.caption(f"reasoning: `{effort_label}`")
        edited[role] = dict(raw)
        edited[role]["model"] = col2.text_input(
            f"{role} model",
            value=str(raw.get("model") or ""),
            key=f"model_{selected_id}_{role}",
            label_visibility="collapsed",
        )

    if st.button("ŞİMDİ DEVAM ET", type="primary", use_container_width=True):
        stop_path.unlink(missing_ok=True)
        state = ResearchState(project)
        trace = Trace("theorem-resume")
        project_info = pm.get(selected_id)
        trace.log(
            "project_context",
            project_id=selected_id,
            project_uuid=project_info.project_uuid,
            title=project_info.title,
            experiment="Teorem Araştırması",
        )
        agents = {}
        for role, raw in edited.items():
            agents[role] = Agent(
                name=str(raw.get("name") or role),
                system_prompt=str(raw.get("system_prompt") or ""),
                model=str(raw.get("model") or ""),
                temperature=float(raw.get("temperature", 0.2)),
                max_tokens=raw.get("max_tokens"),
                reasoning_effort=raw.get("reasoning_effort"),
            )
        required = {
            "ResearchManager",
            "Theorist",
            "AdversarialCritic",
            "VerificationEngineer",
            "IndependentAuditor",
        }
        missing = required - set(agents)
        if missing:
            trace.close()
            st.error(f"Eksik rol config'i: {sorted(missing)}")
        else:
            status = st.status("Araştırma kaldığı yerden devam ediyor...", expanded=True)
            try:
                pm.touch(selected_id, status="RUNNING")
                frozen = state.frozen_problem() or {}
                lab = TheoremResearchLab(
                    trace,
                    state,
                    toolbox=ResearchToolbox(),
                    code_experiment_settings_override=config.get("code_experiment") or None,
                )
                result = lab.run(
                    str(config.get("problem") or frozen.get("problem", "")),
                    manager=agents["ResearchManager"],
                    proposer=agents["Theorist"],
                    code_agent=agents.get("CodeExperimentAgent"),
                    critic=agents["AdversarialCritic"],
                    verifier=agents["VerificationEngineer"],
                    literature_agent=agents.get("LiteratureScout"),
                    auditor=agents["IndependentAuditor"],
                    iterations=int(config.get("iterations", 5)),
                    literature_query=config.get("literature_query"),
                    checkpoint_every=int(config.get("checkpoint_every", 2)),
                )
                new_runtime = read_json(runtime_path, {})
                new_status = str(new_runtime.get("status") or "COMPLETED")
                pm.touch(selected_id, status=new_status)
                status.update(
                    label="Devam çalışması tamamlandı/beklemeye alındı",
                    state="complete",
                )
                st.markdown(result)
            except ProjectBusyError as exc:
                status.update(label="Proje zaten başka bir process tarafından çalıştırılıyor", state="error")
                st.error(str(exc))
            except Exception as exc:
                pm.touch(selected_id, status="PAUSED_ERROR")
                status.update(label="Resume çağrısı beklenmeyen hata verdi", state="error")
                st.exception(exc)
            finally:
                if not trace.closed:
                    summary_path = trace.close()
                    st.caption(f"Resume logları: {summary_path.parent}")

with st.expander("Kaydedilmiş run config", expanded=False):
    st.json(config)
with st.expander("Runtime cursor", expanded=False):
    st.json(runtime)
with st.expander("Partial/soft-resume adımları", expanded=False):
    st.json(partials)
with st.expander("Step cache anahtarları", expanded=False):
    st.json(
        {
            k: {
                "status": v.get("status"),
                "model": v.get("model"),
                "fingerprint": str(v.get("fingerprint") or "")[:16],
            }
            if isinstance(v, dict)
            else v
            for k, v in cache.items()
        }
    )
