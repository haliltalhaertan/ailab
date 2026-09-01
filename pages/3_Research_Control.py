import json
from pathlib import Path

import streamlit as st

from lab import Agent, ResearchState, ResearchToolbox, TheoremResearchLab, Trace

ROOT = Path("research_state")

st.set_page_config(page_title="Araştırma Kontrolü", layout="wide")
st.title("Araştırma Kontrolü")
st.caption(
    "Aktif araştırmayı güvenli biçimde durdurabilir ve aynı project_id için ilk tamamlanmamış "
    "agent/tool adımından devam ettirebilirsin. Tamamlanmış adımlar tekrar API çağrısı yapmaz."
)


def projects() -> list[Path]:
    if not ROOT.exists():
        return []
    return sorted(
        [p for p in ROOT.iterdir() if p.is_dir() and (p / "problem_frozen.json").exists()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


items = projects()
if not items:
    st.info("Henüz kalıcı theorem research projesi yok.")
    st.stop()

project = st.selectbox("Project ID", items, format_func=lambda p: p.name)
runtime_path = project / "runtime.json"
config_path = project / "run_config.json"
stop_path = project / "stop.flag"
cache_path = project / "step_cache.json"

runtime = read_json(runtime_path, {})
config = read_json(config_path, {})
cache = read_json(cache_path, {})

c1, c2, c3, c4 = st.columns(4)
c1.metric("Durum", runtime.get("status", "UNKNOWN"))
c2.metric("Tamamlanan tur", runtime.get("completed_iterations", 0))
c3.metric("Şu anki tur", runtime.get("current_iteration", 0))
c4.metric("Tamamlanan adım", len([v for v in cache.values() if isinstance(v, dict) and v.get("status") == "COMPLETE"]))

st.write("**Şu anki adım:**", runtime.get("current_step", "-"))
if runtime.get("next_task"):
    st.write("**Sonraki araştırma hedefi:**", runtime["next_task"])
if runtime.get("last_error"):
    st.error(runtime["last_error"])

left, right = st.columns(2)
with left:
    if st.button("DURDUR", type="primary", use_container_width=True):
        stop_path.write_text("stop requested\n", encoding="utf-8")
        st.warning(
            "Durdurma isteği yazıldı. Aktif LLM stream'i bir sonraki gelen parçada, aksi halde "
            "bir sonraki adım sınırında duracak. Tamamlanan işler korunur."
        )

with right:
    if st.button("Durdurma isteğini iptal et", use_container_width=True):
        stop_path.unlink(missing_ok=True)
        st.success("Stop flag kaldırıldı.")

st.divider()
st.subheader("Kaldığı yerden devam et")

if not config:
    st.warning(
        "Bu proje eski sürümle oluşturulmuş ve run_config.json yok. Ana sayfada aynı project_id ile "
        "model seçimlerini yapıp Deneyi Çalıştır'a bas; resumable workflow kaldığı yerden devam eder."
    )
else:
    st.caption(
        "Aşağıdaki modeller son kaydedilen run config'den gelir. İstersen 404 veren model slug'ını "
        "burada değiştirerek devam edebilirsin."
    )
    edited = {}
    for role, raw in config.get("agents", {}).items():
        col1, col2 = st.columns([1, 2])
        col1.write(f"**{role}**")
        edited[role] = dict(raw)
        edited[role]["model"] = col2.text_input(
            f"{role} model",
            value=str(raw.get("model") or ""),
            key=f"model_{project.name}_{role}",
            label_visibility="collapsed",
        )

    if st.button("ŞİMDİ DEVAM ET", type="primary", use_container_width=True):
        stop_path.unlink(missing_ok=True)
        state = ResearchState(project)
        trace = Trace("theorem-resume")
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
                result = TheoremResearchLab(
                    trace,
                    state,
                    toolbox=ResearchToolbox(),
                ).run(
                    str(config.get("problem") or state.frozen_problem().get("problem", "")),
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
                status.update(label="Devam çalışması tamamlandı/beklemeye alındı", state="complete")
                st.markdown(result)
            except Exception as exc:
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
    st.json({k: {"status": v.get("status"), "model": v.get("model")} if isinstance(v, dict) else v for k, v in cache.items()})
