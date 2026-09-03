from __future__ import annotations

import os
import shutil

import streamlit as st
from dotenv import load_dotenv

from lab.code_experiment_settings import load_code_experiment_settings, save_code_experiment_settings
from lab.openrouter_catalog import fetch_openrouter_models

load_dotenv()
st.set_page_config(page_title="Code Deneyi Ayarları", layout="wide")
st.title("Code Experiment Agent")
st.caption("LLM'nin ürettiği Python host üzerinde çalıştırılmaz; Docker/Podman içinde no-network disposable container kullanılır.")


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
filtered = [m for m in models if not needle or needle in str(m.get("id", "")).casefold() or needle in str(m.get("label", "")).casefold()]
configured = str(settings.get("model") or os.environ.get("LAB_CODE_EXPERIMENT_MODEL") or "").strip()
ids = [str(m.get("id")) for m in filtered if m.get("id")]
labels = {str(m.get("id")): str(m.get("label") or m.get("id")) for m in filtered if m.get("id")}
if configured and configured not in ids and not query:
    ids.insert(0, configured)
    labels[configured] = configured
selected = configured
if ids:
    default = configured if configured in ids else ids[0]
    selected = st.selectbox("CodeExperimentAgent modeli", ids, index=ids.index(default), format_func=lambda mid: labels.get(mid, mid))
manual = st.text_input("Manuel model ID (opsiyonel)", value="", placeholder="örn. z-ai/glm-5.3").strip()
model = manual or selected

found_docker = shutil.which("docker")
found_podman = shutil.which("podman")
if found_docker or found_podman:
    st.success(f"Container engine bulundu: `{found_docker or found_podman}`")
else:
    st.error("Docker/Podman bulunamadı. CodeExperimentAgent kod yazabilir ama güvenlik nedeniyle run_python çalıştırılmaz.")

c1, c2, c3 = st.columns(3)
max_steps = c1.number_input("Maksimum LLM action", 1, 20, int(settings.get("max_steps", 8)))
timeout_s = c2.number_input("Python timeout (sn)", 5, 600, int(settings.get("timeout_s", 60)))
memory_limit_mb = c3.number_input("Container RAM (MB)", 128, 8192, int(settings.get("memory_limit_mb", 768)), step=128)
c4, c5, c6 = st.columns(3)
max_output_mb = c4.number_input("Stdout+stderr limiti (MB)", 1, 64, int(settings.get("max_output_mb", 4)))
pid_limit = c5.number_input("PID limiti", 1, 128, int(settings.get("pid_limit", 8)))
cpu_limit = c6.number_input("CPU limiti", 0.1, 16.0, float(settings.get("cpu_limit", 1.0)), step=0.1)
engine = st.selectbox("Container engine", ["auto", "docker", "podman"], index=0 if not settings.get("container_engine") else (["auto", "docker", "podman"].index(settings.get("container_engine")) if settings.get("container_engine") in {"docker", "podman"} else 0))
image = st.text_input("Container image", value=str(settings.get("container_image") or "python:3.12-slim"))

st.info(
    "Container güvenlik profili: `--network=none`, read-only rootfs, `--cap-drop=ALL`, `no-new-privileges`, RAM/PID/CPU limitleri ve yalnız proje workspace'inin writable mount edilmesi. "
    "AST filtresi yalnız defense-in-depth'tir; güvenlik sınırı container'dır."
)

if st.button("Code deney ayarlarını kaydet", type="primary", width="stretch"):
    path = save_code_experiment_settings(
        {
            "model": model,
            "max_steps": int(max_steps),
            "timeout_s": int(timeout_s),
            "memory_limit_mb": int(memory_limit_mb),
            "max_output_mb": int(max_output_mb),
            "pid_limit": int(pid_limit),
            "cpu_limit": float(cpu_limit),
            "container_engine": "" if engine == "auto" else engine,
            "container_image": image.strip() or "python:3.12-slim",
        }
    )
    st.success(f"Kaydedildi: {path}")
