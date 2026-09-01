import json
from pathlib import Path

import streamlit as st

from lab import Agent, ResearchState, ResearchToolbox, TheoremResearchLab, Trace
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
runtime = read_json(runtime_path, {})
config = read_json(config_path, {})
cache = read_json(cache_path, {})

c1, c2, c3, c4 = st.columns(4)
c1.metric("Durum", runtime.get("status", "READY"))
c2.metric("Tamamlanan tur", runtime.get("completed_iterations", 0))
c3.metric("Şu anki tur", runtime.get("current_iteration", 0))
c4.metric(
    "Tamamlanan adım",
    len([v for v in cache.values() if isinstance(v, dict) and v.get("status") == "COMPLETE"]),
)
st.write("**Şu anki adım:**", runtime.get("current_step", "-"))
if runtime.get("next_task"):
    st.write("**Sonraki araştırma hedefi:**", runtime["next_task"])
if runtime.get("last_error"):
    st.error(runtime["last_error"])

left, right = st.columns(2)
with left:
    if st.button("DURDUR", type="primary", use_container_width=True):
        stop_path.write_text("stop requested\n", encoding="utf-8")
        st.warning("Durdurma isteği yazıldı. Tamamlanan işler korunacak.")
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
        "404 veren model slug'ı gibi ayarları burada değiştirip devam edebilirsin. "
        "Reasoning effort kalıcı agent ayarlarından yüklenir."
    )
    edited = {}
    for role, raw in config.get("agents", {}).items():
        col1, col2 = st.columns([1, 2])
        col1.write(f"**{role}**")
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
        trace.log(
            "project_context",
            project_id=selected_id,
            title=pm.get(selected_id).title,
            experiment="Teorem Araştırması",
        )
        pm.touch(selected_id, status="RUNNING")
        agents = {}
        for role, raw in edited.items():
            agents[role] = Agent(
                name=str(raw.get("name") or role),
                system_prompt=str(raw.get("system_prompt") or ""),
                model=str(raw.get("model") or ""),
                temperature=float(raw.get("temperature", 0.2)),
                max_tokens=raw.get("max_tokens"),
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
            st.error(f"Eksik rol config'i: {sorted(missing)}")
        else:
            status = st.status("Araştırma kaldığı yerden devam ediyor...", expanded=True)
            try:
                frozen = state.frozen_problem() or {}
                result = TheoremResearchLab(trace, state, toolbox=ResearchToolbox()).run(
                    str(config.get("problem") or frozen.get("problem", "")),
                    manager=agents["ResearchManager"],
                    proposer=agents["Theorist"],
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
            except Exception as exc:
                pm.touch(selected_id, status="PAUSED_ERROR")
                status.update(label="Resume çağrısı beklenmeyen hata verdi", state="error")
                st.exception(exc)
            finally:
                summary_path = trace.close()
                st.caption(f"Resume logları: {summary_path.parent}")

with st.expander("Kaydedilmiş run config", expanded=False):
    st.json(config)
with st.expander("Runtime cursor", expanded=False):
    st.json(runtime)
with st.expander("Step cache anahtarları", expanded=False):
    st.json(
        {
            k: {"status": v.get("status"), "model": v.get("model")}
            if isinstance(v, dict)
            else v
            for k, v in cache.items()
        }
    )
