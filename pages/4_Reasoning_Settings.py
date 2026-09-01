from __future__ import annotations

import streamlit as st

from lab.reasoning_settings import API_TO_UI, UI_LEVELS, UI_TO_API, load_settings, save_settings


st.set_page_config(page_title="Reasoning Ayarları", layout="wide")
st.title("Reasoning Ayarları")
st.caption(
    "Her agent için OpenRouter reasoning effort seviyesini ayrı seç. "
    "Max arayüzdeki isimdir; OpenRouter'a xhigh olarak gönderilir."
)

ROLES = [
    "ProjectPlanner",
    "ResearchManager",
    "Theorist",
    "AdversarialCritic",
    "VerificationEngineer",
    "LiteratureScout",
    "IndependentAuditor",
    "Teorisyen",
    "Sceptik",
    "Raporcu",
    "Taraftar A",
    "Taraftar B",
    "Hakem",
    "Araştırmacı",
    "Analist",
    "Eleştirmen",
    "Panelist",
    "Sentezleyici",
]

settings = load_settings()
agents = settings.setdefault("agents", {})

st.info(
    "Provider default seçersen effort parametresi gönderilmez. "
    "None reasoning'i kapatmayı ister. Bir model belirli seviyeyi desteklemiyorsa provider davranışı modele göre değişebilir."
)

preset = st.selectbox(
    "Tüm agentlara hızlı uygula",
    ["Değiştirme"] + UI_LEVELS,
    index=0,
)
if st.button("Tümüne uygula", disabled=preset == "Değiştirme"):
    api_value = UI_TO_API[preset]
    for role in ROLES:
        agents[role] = api_value
    save_settings(settings)
    st.success(f"Tüm agentlar: {preset}")
    st.rerun()

st.divider()

edited = dict(agents)
for role in ROLES:
    current_api = agents.get(role)
    current_label = API_TO_UI.get(current_api, "Provider default")
    cols = st.columns([1.5, 2.0, 2.0])
    cols[0].markdown(f"**{role}**")
    selected = cols[1].selectbox(
        f"{role} effort",
        UI_LEVELS,
        index=UI_LEVELS.index(current_label),
        key=f"effort_{role}",
        label_visibility="collapsed",
    )
    api_value = UI_TO_API[selected]
    edited[role] = api_value
    cols[2].caption(
        "provider default" if api_value is None else f"OpenRouter: reasoning.effort={api_value}"
    )

if st.button("Reasoning ayarlarını kaydet", type="primary", use_container_width=True):
    settings["agents"] = edited
    path = save_settings(settings)
    st.success(f"Kaydedildi: {path}")

with st.expander("Ham reasoning settings", expanded=False):
    st.json({"agents": edited})
