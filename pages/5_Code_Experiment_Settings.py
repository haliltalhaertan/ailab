from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from lab.code_experiment_settings import load_code_experiment_settings, save_code_experiment_settings
from lab.openrouter_catalog import fetch_openrouter_models


load_dotenv()
st.set_page_config(page_title="Code Deneyi Ayarları", layout="wide")
st.title("Code Experiment Agent")
st.caption(
    "LLM'nin proje workspace'i içinde Python deneyi yazıp çalıştırdığı kontrollü araştırma katmanını ayarla."
)


@st.cache_data(ttl=600, show_spinner=False)
def catalog():
    return [m.as_dict() | {"label": m.label} for m in fetch_openrouter_models()]


settings = load_code_experiment_settings()
try:
    models = catalog()
except Exception as exc:
    models = []
    st.warning(f"OpenRouter kataloğu alınamadı: {exc}")

query = st.text_input("Model ara", placeholder="örn. glm 5.3, kimi, coder")
needle = query.strip().casefold()
filtered = [
    m for m in models
    if not needle
    or needle in str(m.get("id", "")).casefold()
    or needle in str(m.get("label", "")).casefold()
]
if query:
    st.caption(f"{len(filtered)} model eşleşti")

configured = str(settings.get("model") or os.environ.get("LAB_CODE_EXPERIMENT_MODEL") or "").strip()
ids = [str(m.get("id")) for m in filtered if m.get("id")]
labels = {str(m.get("id")): str(m.get("label") or m.get("id")) for m in filtered if m.get("id")}
if configured and configured not in ids and not query:
    ids.insert(0, configured)
    labels[configured] = configured

if ids:
    default = configured if configured in ids else ids[0]
    selected = st.selectbox(
        "CodeExperimentAgent modeli",
        ids,
        index=ids.index(default),
        format_func=lambda mid: labels.get(mid, mid),
    )
else:
    selected = configured
    st.info("Katalogdan model seçilemedi; aşağıya manuel slug yazabilirsin.")

manual = st.text_input(
    "Manuel model ID (opsiyonel)",
    value="",
    placeholder="örn. z-ai/glm-5.3",
).strip()
model = manual or selected

c1, c2 = st.columns(2)
max_steps = c1.number_input(
    "Bir deneyde maksimum LLM action sayısı",
    min_value=1,
    max_value=20,
    value=int(settings.get("max_steps", 8)),
)
timeout_s = c2.number_input(
    "Tek Python çalıştırması timeout (sn)",
    min_value=5,
    max_value=300,
    value=int(settings.get("timeout_s", 60)),
)

c3, c4 = st.columns(2)
memory_limit_mb = c3.number_input(
    "Python process ağacı bellek limiti (MB)",
    min_value=128,
    max_value=8192,
    value=int(settings.get("memory_limit_mb", 768)),
)
max_output_mb = c4.number_input(
    "stdout + stderr toplam limiti (MB)",
    min_value=1,
    max_value=64,
    value=int(settings.get("max_output_mb", 4)),
)

st.info(
    "İzin verilen actions: write_file, patch_file, read_file, list_files, run_python, finish. "
    "finish için en az bir gerçek başarılı run_python zorunludur ve son Python denemesi başarılı olmalıdır. "
    "Computation evidence otomatik ispat sayılmaz."
)

if st.button("Code deney ayarlarını kaydet", type="primary", use_container_width=True):
    path = save_code_experiment_settings(
        {
            "model": model,
            "max_steps": int(max_steps),
            "timeout_s": int(timeout_s),
            "memory_limit_mb": int(memory_limit_mb),
            "max_output_mb": int(max_output_mb),
        }
    )
    st.success(f"Kaydedildi: {path}")

with st.expander("Güvenlik modeli", expanded=False):
    st.markdown(
        "- Workspace: `research_state/<project_id>/workspace/`\n"
        "- Shell/PowerShell/CMD action yok.\n"
        "- API key ve çoğu environment variable child process'e aktarılmaz.\n"
        "- Python `-I` modunda, AST güvenlik kontrolünden sonra çalışır.\n"
        "- `os`, `subprocess`, `socket`, dosya `open`, `eval/exec` ve dunder introspection engellenir.\n"
        "- stdout/stderr RAM'de sınırsız biriktirilmez; immutable evidence dosyalarına akar ve byte limiti izlenir.\n"
        "- Process ağacı psutil ile bellek/PID sınırına karşı izlenir; timeout veya DURDUR isteğinde terminate edilir.\n"
        "- Opsiyonel `numpy/sympy/networkx` yalnız gerçekten kuruluysa capability listesine girer.\n"
        "- Bu hâlâ VM/container seviyesinde network/filesystem namespace izolasyonu değildir; yüksek tehdit modeli için dış container runner tercih edilmelidir."
    )
