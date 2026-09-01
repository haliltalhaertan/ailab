import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lab import Agent, Orchestrator, Trace

load_dotenv()

RUNS_DIR = Path("runs")

MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "deepseek/deepseek-r1",
    "anthropic/claude-3.5-sonnet",
    "google/gemini-2.0-flash-001",
    "meta-llama/llama-3.3-70b-instruct",
]

ROLE_LIBRARY = {
    "Teorisyen": (
        "Sen matematik ve bilgisayar bilimi alanında yaratıcı bir teorisyensin. "
        "Problemlere birden fazla yaklaşımla saldır; varsayımlarını açıkça belirt, "
        "ispat taslakları, sözde-kod ve küçük örnekler ver. Emin olmadığın yerleri "
        "'varsayım' veya 'açık soru' olarak etiketle."
    ),
    "Sceptik": (
        "Sen acımasız bir hakemsin. Hatalı adımları, çürük ispatları ve gizli "
        "varsayımları bulmaya çalış; karşıörnekler üret, sınır durumlarını kontrol et, "
        "bilinen teoremlerle çelişkileri işaretle. Eleştirilerin numaralı ve somut olsun; "
        "nazik olma ama adil ol."
    ),
    "Raporcu": (
        "Sen bir araştırma raporu yazarısın. Tartışmayı taraf tutmadan, verilen "
        "yapıda sade ve keskin bir rapora dönüştürürsün."
    ),
    "Taraftar A": "Sen 'A' pozisyonunun tutkulu bir savunucususun. En güçlü argümanlarını verilerle destekle.",
    "Taraftar B": "Sen 'B' pozisyonunun tutkulu bir savunucususun. Rakibin argümanlarını çürüt, kendi pozisyonunu güçlendir.",
    "Hakem": "Sen tarafsız bir hakemsin. Tartışmayı objektif kriterlerle değerlendirip kazananı ve gerekçesini açıkla.",
    "Araştırmacı": "Sen bir araştırmacısın. Verilen konu hakkında olgusal, yapılandırılmış bir arka plan raporu yaz. Spekülasyon yapma, eksik noktaları belirt.",
    "Analist": "Sen bir analistsin. Sana verilen araştırma notlarını eleştirel bir şekilde analiz et, fırsat ve riskleri listele.",
    "Eleştirmen": "Sen bir eleştirmensin. Analizi denetle: zayıf varsayımları, eksik verileri ve mantık hatalarını işaretle ve son bir özet ver.",
    "Panelist": "Sen deneyimli bir panelistsin. Soruya kendi uzmanlık alanından, net ve özgün bir bakış açısıyla yanıt ver.",
    "Sentezleyici": "Sen bir sentezleyicisin. Farklı cevapları taraf tutmadan tek bir tutarlı yanıtta birleştirirsin.",
}

ROLE_TEMPS = {
    "Teorisyen": 0.8,
    "Sceptik": 0.3,
    "Raporcu": 0.4,
    "Taraftar A": 0.9,
    "Taraftar B": 0.9,
    "Hakem": 0.3,
    "Panelist": 0.7,
    "Sentezleyici": 0.4,
}

ROLE_MODELS = {
    "Teorisyen": "deepseek/deepseek-r1",
    "Taraftar A": "deepseek/deepseek-r1",
    "Sceptik": "anthropic/claude-3.5-sonnet",
}

EXPERIMENTS = {
    "Araştırma Döngüsü": {
        "method": "research_loop",
        "slug": "research",
        "roles": ["Teorisyen", "Sceptik", "Raporcu"],
        "optional_roles": ["Raporcu"],
        "param_label": "Tur sayısı",
        "param_default": 3,
        "prompt_label": "Araştırma problemi",
        "default_prompt": (
            "Büyük dil modellerinin ürettiği matematiksel ispatların otomatik "
            "doğrulanabilirliği için bir çerçeve tasarla: doğrulama adımları, "
            "güvenilirlik ölçütleri ve bilinen zayıf noktalarına karşı testler."
        ),
    },
    "Tartışma": {
        "method": "debate",
        "slug": "debate",
        "roles": ["Taraftar A", "Taraftar B", "Hakem"],
        "optional_roles": ["Hakem"],
        "param_label": "Tur sayısı",
        "param_default": 2,
        "prompt_label": "Tartışma konusu",
        "default_prompt": "Yapay zeka geliştirmeyi açık kaynak mı olmalı yoksa kapalı laboratuvarlarda mı yürütmeli?",
    },
    "Zincir": {
        "method": "pipeline",
        "slug": "pipeline",
        "roles": ["Araştırmacı", "Analist", "Eleştirmen"],
        "optional_roles": [],
        "param_label": None,
        "param_default": 0,
        "prompt_label": "Görev",
        "default_prompt": "Uzay turizminin 2050'ye kadar ekonomik etkisini değerlendir.",
    },
    "Panel": {
        "method": "panel",
        "slug": "panel",
        "roles": ["Panelist", "Panelist", "Panelist", "Sentezleyici"],
        "optional_roles": ["Sentezleyici"],
        "param_label": None,
        "param_default": 0,
        "prompt_label": "Soru",
        "default_prompt": "P ve NP probleminin çözülmesi ekonomiyi nasıl etkiler?",
    },
}


class ObservedTrace(Trace):
    def __init__(self, experiment: str, on_call=None):
        super().__init__(experiment)
        self.on_call = on_call

    def agent_call(self, agent, model, temperature, messages, response):
        super().agent_call(agent, model, temperature, messages, response)
        if self.on_call:
            self.on_call(agent, response)


def build_sidebar(exp_name):
    exp = EXPERIMENTS[exp_name]
    prompt = st.sidebar.text_area(exp["prompt_label"], value=exp["default_prompt"], height=130)
    param = None
    if exp["param_label"]:
        param = st.sidebar.number_input(exp["param_label"], min_value=1, max_value=10, value=exp["param_default"])
    agents, optional = [], {}
    for i, role in enumerate(exp["roles"]):
        is_optional = role in exp["optional_roles"]
        key = f"{exp_name}_{i}"
        if is_optional and not st.sidebar.checkbox(f"{role} dahil et", value=True, key=f"inc_{key}"):
            continue
        with st.sidebar.expander(role, expanded=False):
            sys_prompt = st.text_area(
                "Sistem promptu",
                ROLE_LIBRARY.get(role, ROLE_LIBRARY["Panelist"]),
                key=f"p_{key}",
                height=150,
            )
            default_model = ROLE_MODELS.get(role, "openai/gpt-4o-mini")
            model = st.selectbox("Model", MODELS, index=MODELS.index(default_model), key=f"m_{key}")
            temp = st.slider("Sıcaklık", 0.0, 1.5, ROLE_TEMPS.get(role, 0.7), 0.05, key=f"t_{key}")
        cfg = {"role": role, "prompt": sys_prompt, "model": model, "temp": temp}
        if is_optional:
            optional[role] = cfg
        else:
            agents.append(cfg)
    return prompt, param, agents, optional


def execute(exp_name, prompt, param, agents, optional):
    exp = EXPERIMENTS[exp_name]
    steps = []
    status = st.status("Deney çalışıyor...", expanded=True)

    def on_call(agent, resp):
        line = f"{agent} tamamlandı: {resp.prompt_tokens + resp.completion_tokens} token, {resp.latency_s} sn"
        steps.append(line)
        with status.container():
            st.write(line)

    trace = ObservedTrace(exp["slug"], on_call=on_call)
    orch = Orchestrator(trace)
    a_objs = [
        Agent(name=c["role"], system_prompt=c["prompt"], model=c["model"], temperature=c["temp"])
        for c in agents
    ]
    o_objs = {
        r: Agent(name=r, system_prompt=c["prompt"], model=c["model"], temperature=c["temp"])
        for r, c in optional.items()
    }
    try:
        method = exp["method"]
        if method == "research_loop":
            result = orch.research_loop(prompt, a_objs[0], a_objs[1], iterations=param, synthesizer=o_objs.get("Raporcu"))
        elif method == "debate":
            result = orch.debate(prompt, a_objs[:2], rounds=param, judge=o_objs.get("Hakem"))
        elif method == "pipeline":
            result = orch.pipeline(prompt, a_objs)
        else:
            result = orch.panel(prompt, a_objs, synthesizer=o_objs.get("Sentezleyici"))
    except Exception as e:
        status.update(label="Hata oluştu", state="error", expanded=True)
        st.error(f"Deney başarısız: {e}")
        return None
    status.update(label="Tamamlandı", state="complete")
    summary_path = trace.close()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "exp": exp_name,
        "result": result,
        "summary": summary,
        "run_dir": str(trace.run_dir),
        "steps": steps,
    }


def render_result(last):
    s = last["summary"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Toplam çağrı", s["total_calls"])
    c2.metric("Toplam token", s["total_tokens"])
    c3.metric("Toplam süre (sn)", round(sum(v["latency_s"] for v in s["agents"].values()), 1))
    st.caption(f"Kayıt klasörü: {last['run_dir']}")
    st.subheader("Sonuç")
    st.markdown(last["result"])
    with st.expander("Çalışma günlüğü"):
        for line in last["steps"]:
            st.write(line)


def render_history():
    traces = sorted(RUNS_DIR.glob("*/trace.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if RUNS_DIR.exists() else []
    if not traces:
        st.info("Henüz kayıt yok. Deney sekmesinden ilk deneyini çalıştır.")
        return
    sel = st.selectbox("Deney kaydı", [t.parent for t in traces], format_func=lambda p: p.name)
    summary_file = sel / "summary.json"
    if summary_file.exists():
        s = json.loads(summary_file.read_text(encoding="utf-8"))
        c1, c2 = st.columns(2)
        c1.metric("Toplam çağrı", s["total_calls"])
        c2.metric("Toplam token", s["total_tokens"])
        rows = [
            {
                "Ajan": name,
                "Çağrı": v["calls"],
                "Prompt token": v["prompt_tokens"],
                "Tamamlama token": v["completion_tokens"],
                "Süre (sn)": round(v["latency_s"], 1),
            }
            for name, v in s["agents"].items()
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    events = []
    for line in (sel / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("type") != "llm_call":
            continue
        events.append(
            {
                "Ajan": ev["agent"],
                "Model": ev["model"],
                "Token": ev["prompt_tokens"] + ev["completion_tokens"],
                "Süre (sn)": ev["latency_s"],
                "Çıktı": ev["output"][:200],
            }
        )
    st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
    with st.expander("Tam çıktılar"):
        for ev in events:
            st.markdown(f"**{ev['Ajan']}** ({ev['Model']})")
            st.text(ev["Çıktı"])


def main():
    st.title("LLM Araştırma Laboratuvarı")
    st.sidebar.header("Deney Ayarları")
    exp_name = st.sidebar.selectbox("Deney tipi", list(EXPERIMENTS))
    prompt, param, agents, optional = build_sidebar(exp_name)

    api_ok = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    if not api_ok:
        st.sidebar.error("OPENROUTER_API_KEY bulunamadı. llm-lab/.env dosyasına ekleyip uygulamayı yeniden başlat.")

    tab_run, tab_hist = st.tabs(["Deney", "Geçmiş Kayıtlar"])

    with tab_run:
        if st.button("Deneyi Çalıştır", type="primary", disabled=not api_ok, use_container_width=True):
            if not prompt.strip():
                st.warning("Lütfen bir problem/konu gir.")
            else:
                data = execute(exp_name, prompt, param, agents, optional)
                if data:
                    st.session_state["last"] = data
        last = st.session_state.get("last")
        if last and last["exp"] == exp_name:
            render_result(last)

    with tab_hist:
        render_history()


if __name__ == "__main__":
    main()
