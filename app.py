import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from lab import Agent, Orchestrator, ResearchState, ResearchToolbox, TheoremResearchLab, Trace
from lab.openrouter_catalog import fetch_openrouter_models

load_dotenv()

RUNS_DIR = Path("runs")

FALLBACK_MODELS = [
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
        "bilinen teoremlerle çelişkileri işaretle. Eleştirilerin numaralı ve somut olsun."
    ),
    "Raporcu": (
        "Sen bir araştırma raporu yazarısın. Tartışmayı taraf tutmadan, verilen "
        "yapıda sade ve keskin bir rapora dönüştürürsün."
    ),
    "Taraftar A": "Sen 'A' pozisyonunun güçlü savunucususun. En iyi argümanlarını verilerle destekle.",
    "Taraftar B": "Sen 'B' pozisyonunun güçlü savunucususun. Rakibin argümanlarını çürüt, kendi pozisyonunu güçlendir.",
    "Hakem": "Sen tarafsız bir hakemsin. Tartışmayı objektif kriterlerle değerlendirip kazananı ve gerekçesini açıkla.",
    "Araştırmacı": "Sen bir araştırmacısın. Verilen konu hakkında olgusal, yapılandırılmış bir arka plan raporu yaz. Spekülasyon yapma, eksik noktaları belirt.",
    "Analist": "Sen bir analistsin. Sana verilen araştırma notlarını eleştirel bir şekilde analiz et, fırsat ve riskleri listele.",
    "Eleştirmen": "Sen bir eleştirmensin. Analizi denetle: zayıf varsayımları, eksik verileri ve mantık hatalarını işaretle ve son bir özet ver.",
    "Panelist": "Sen deneyimli bir panelistsin. Soruya kendi uzmanlık alanından, net ve özgün bir bakış açısıyla yanıt ver.",
    "Sentezleyici": "Sen bir sentezleyicisin. Farklı cevapları taraf tutmadan tek bir tutarlı yanıtta birleştirirsin.",
    "ResearchManager": (
        "Sen araştırma yöneticisisin. Yeni fikir üretmekten çok doğru dalı seç, FAIL fikirleri kapalı tut, "
        "kanıt standardını koru ve her tur tek bir kesin sonraki görev ver."
    ),
    "Theorist": (
        "Sen yaratıcı bir teorik bilgisayar bilimcisin. Küçük, test edilebilir lemma/construction üret. "
        "Bilinen sonucu yeniden keşfetmekten kaçın; varsayımı açıkça etiketle."
    ),
    "AdversarialCritic": (
        "Sen bağımsız adversarial matematik hakemisin. Önceliğin çözümü geliştirmek değil çürütmektir: "
        "karşıörnek, gizli varsayım, yanlış model ve asymptotic hata ara."
    ),
    "VerificationEngineer": (
        "Sen doğrulama mühendisisin. LLM kanaatini ispat sayma. Hangi deterministic test, Z3 sorgusu, "
        "küçük-n enumeration veya formal proof gerektiğini belirt."
    ),
    "LiteratureScout": (
        "Sen novelty/literatür tarama ajanısın. Sadece verilen bibliyografik adaylar hakkında emin olduğun kadar konuş; "
        "kanıt veya theorem içeriği uydurma. Benzer sonuçları ve aranması gereken terimleri işaretle."
    ),
    "IndependentAuditor": (
        "Sen araştırma ekibinden bağımsız sıfır-güven denetçisisin. Kanıt yükünü yüksek tut; "
        "OPEN ile PROVEN'i kesin ayır ve novelty risklerini işaretle."
    ),
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
    "ResearchManager": 0.2,
    "Theorist": 0.8,
    "AdversarialCritic": 0.2,
    "VerificationEngineer": 0.1,
    "LiteratureScout": 0.1,
    "IndependentAuditor": 0.1,
}

ROLE_MODELS = {
    "Teorisyen": "deepseek/deepseek-r1",
    "Taraftar A": "deepseek/deepseek-r1",
    "Sceptik": "anthropic/claude-3.5-sonnet",
    "ResearchManager": "openai/gpt-4o",
    "Theorist": "deepseek/deepseek-r1",
    "AdversarialCritic": "anthropic/claude-3.5-sonnet",
    "VerificationEngineer": "openai/gpt-4o",
    "LiteratureScout": "openai/gpt-4o-mini",
    "IndependentAuditor": "google/gemini-2.0-flash-001",
}

ROLE_MODEL_ENV = {
    "ResearchManager": "LAB_MANAGER_MODEL",
    "Theorist": "LAB_PROPOSER_MODEL",
    "AdversarialCritic": "LAB_CRITIC_MODEL",
    "VerificationEngineer": "LAB_VERIFIER_MODEL",
    "LiteratureScout": "LAB_LITERATURE_MODEL",
    "IndependentAuditor": "LAB_AUDITOR_MODEL",
}

TROPICAL_PROBLEM = (
    "Let P_n be the simple s-t path provenance polynomial of K_n over the min-plus tropical semiring. "
    "Improve either the known O(n^3) circuit upper bound or the trivial Omega(n^2) lower bound, "
    "or isolate a new rigorous barrier/subclass result."
)

EXPERIMENTS = {
    "Teorem Araştırması": {
        "method": "theorem_lab",
        "slug": "theorem",
        "roles": [
            "ResearchManager",
            "Theorist",
            "AdversarialCritic",
            "VerificationEngineer",
            "LiteratureScout",
            "IndependentAuditor",
        ],
        "optional_roles": [],
        "param_label": "Tur sayısı",
        "param_default": 6,
        "prompt_label": "Araştırma problemi",
        "default_prompt": TROPICAL_PROBLEM,
    },
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


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-")
    return value[:60] or "research"


def default_model(role: str) -> str:
    env_name = ROLE_MODEL_ENV.get(role)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return ROLE_MODELS.get(role, os.environ.get("LAB_MODEL", "openai/gpt-4o-mini"))


class ObservedTrace(Trace):
    def __init__(self, experiment: str, on_call=None):
        super().__init__(experiment)
        self.on_call = on_call

    def agent_call(self, agent, model, temperature, messages, response):
        super().agent_call(agent, model, temperature, messages, response)
        if self.on_call:
            self.on_call(agent, response)


def build_sidebar(exp_name, model_ids, model_labels):
    exp = EXPERIMENTS[exp_name]
    prompt = st.sidebar.text_area(exp["prompt_label"], value=exp["default_prompt"], height=150)
    param = None
    if exp["param_label"]:
        max_turns = 100 if exp["method"] == "theorem_lab" else 10
        param = st.sidebar.number_input(
            exp["param_label"], min_value=1, max_value=max_turns, value=exp["param_default"]
        )

    extras = {}
    if exp["method"] == "theorem_lab":
        extras["project_id"] = slugify(
            st.sidebar.text_input("Project ID", value="tropical-circuit")
        )
        extras["literature_query"] = st.sidebar.text_input(
            "Literatür arama sorgusu",
            value=os.environ.get(
                "LAB_LITERATURE_QUERY",
                "tropical circuit reachability provenance lower bound",
            ),
        )
        extras["checkpoint_every"] = st.sidebar.number_input(
            "Checkpoint sıklığı (tur)", min_value=1, max_value=20, value=2
        )

    agents, optional = [], {}
    role_counts: dict[str, int] = {}
    for i, role in enumerate(exp["roles"]):
        role_counts[role] = role_counts.get(role, 0) + 1
        display_role = role if role_counts[role] == 1 else f"{role} {role_counts[role]}"
        is_optional = role in exp["optional_roles"]
        key = f"{exp_name}_{i}_{role_counts[role]}"
        if is_optional and not st.sidebar.checkbox(
            f"{display_role} dahil et", value=True, key=f"inc_{key}"
        ):
            continue
        with st.sidebar.expander(display_role, expanded=exp["method"] == "theorem_lab"):
            sys_prompt = st.text_area(
                "Sistem promptu",
                ROLE_LIBRARY.get(role, ROLE_LIBRARY["Panelist"]),
                key=f"p_{key}",
                height=135,
            )
            wanted_default = default_model(role)
            choices = list(model_ids)
            if wanted_default not in choices:
                choices.insert(0, wanted_default)
                model_labels.setdefault(wanted_default, wanted_default)
            model = st.selectbox(
                "OpenRouter modeli",
                choices,
                index=choices.index(wanted_default),
                format_func=lambda mid: model_labels.get(mid, mid),
                key=f"m_{key}",
            )
            manual = st.text_input(
                "Manuel model ID (opsiyonel)",
                value="",
                placeholder="örn. z-ai/glm-5.3",
                key=f"manual_{key}",
            ).strip()
            if manual:
                model = manual
            temp = st.slider(
                "Sıcaklık", 0.0, 1.5, ROLE_TEMPS.get(role, 0.7), 0.05, key=f"t_{key}"
            )
            st.caption(f"Kullanılacak model: `{model}`")
        cfg = {
            "role": role,
            "display_role": display_role,
            "prompt": sys_prompt,
            "model": model,
            "temp": temp,
        }
        if is_optional:
            optional[display_role] = cfg
        else:
            agents.append(cfg)
    return prompt, param, agents, optional, extras


def _agent(cfg):
    return Agent(
        name=cfg["display_role"],
        system_prompt=cfg["prompt"],
        model=cfg["model"],
        temperature=cfg["temp"],
    )


def execute(exp_name, prompt, param, agents, optional, extras):
    exp = EXPERIMENTS[exp_name]
    steps = []
    status_box = st.status("Deney çalışıyor...", expanded=True)

    def on_call(agent, resp):
        cost = f"${resp.cost_usd:.6f}" if resp.cost_usd is not None else "ücret N/A"
        line = (
            f"{agent} · {resp.model} · {resp.prompt_tokens + resp.completion_tokens:,} token · "
            f"{cost} · {resp.latency_s:.1f} sn"
        )
        steps.append(line)
        with status_box.container():
            st.write(line)

    trace = ObservedTrace(exp["slug"], on_call=on_call)
    summary_path = None
    try:
        a_objs = [_agent(c) for c in agents]
        o_objs = {r: _agent(c) for r, c in optional.items()}
        method = exp["method"]
        if method == "theorem_lab":
            by_role = {cfg["role"]: _agent(cfg) for cfg in agents}
            project_id = extras["project_id"]
            state = ResearchState(f"research_state/{project_id}")
            trace.log(
                "run_config",
                experiment=exp_name,
                project_id=project_id,
                iterations=int(param),
                models={role: agent.model for role, agent in by_role.items()},
            )
            lab = TheoremResearchLab(trace, state, toolbox=ResearchToolbox())
            result = lab.run(
                prompt,
                manager=by_role["ResearchManager"],
                proposer=by_role["Theorist"],
                critic=by_role["AdversarialCritic"],
                verifier=by_role["VerificationEngineer"],
                literature_agent=by_role["LiteratureScout"],
                auditor=by_role["IndependentAuditor"],
                iterations=int(param),
                literature_query=extras.get("literature_query") or None,
                checkpoint_every=int(extras.get("checkpoint_every", 2)),
            )
        else:
            orch = Orchestrator(trace)
            if method == "research_loop":
                result = orch.research_loop(
                    prompt,
                    a_objs[0],
                    a_objs[1],
                    iterations=int(param),
                    synthesizer=o_objs.get("Raporcu"),
                )
            elif method == "debate":
                result = orch.debate(
                    prompt, a_objs[:2], rounds=int(param), judge=o_objs.get("Hakem")
                )
            elif method == "pipeline":
                result = orch.pipeline(prompt, a_objs)
            else:
                result = orch.panel(prompt, a_objs, synthesizer=o_objs.get("Sentezleyici"))
    except Exception as exc:
        status_box.update(label="Hata oluştu", state="error", expanded=True)
        st.error(f"Deney başarısız: {exc}")
        return None
    finally:
        summary_path = trace.close()

    status_box.update(label="Tamamlandı", state="complete")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "exp": exp_name,
        "result": result,
        "summary": summary,
        "run_dir": str(trace.run_dir),
        "steps": steps,
    }


def cost_text(summary):
    prefix = "" if summary.get("cost_complete", False) else "≥"
    return f"{prefix}${float(summary.get('total_cost_usd', 0.0)):.6f}"


def render_result(last):
    s = last["summary"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Toplam çağrı", s.get("total_calls", 0))
    c2.metric("Toplam token", f"{s.get('total_tokens', 0):,}")
    c3.metric("Toplam ücret", cost_text(s))
    c4.metric("Geçen süre", f"{float(s.get('wall_time_s', 0)):.1f} sn")
    if not s.get("cost_complete", False):
        st.caption("Ücret işaretli ise bazı provider yanıtlarında exact cost alanı dönmemiş olabilir; gösterilen değer bilinen minimum toplamdır.")
    st.caption(f"Kayıt klasörü: {last['run_dir']}")

    rows = []
    for name, v in s.get("agents", {}).items():
        rows.append(
            {
                "Ajan": name,
                "Model": ", ".join(v.get("models", [])),
                "Çağrı": v.get("calls", 0),
                "Input token": v.get("prompt_tokens", 0),
                "Output token": v.get("completion_tokens", 0),
                "Reasoning token": v.get("reasoning_tokens", 0),
                "Cached token": v.get("cached_tokens", 0),
                "Toplam token": v.get("total_tokens", 0),
                "Ücret ($)": round(float(v.get("cost_usd", 0)), 8),
                "LLM süre (sn)": round(float(v.get("latency_s", 0)), 2),
            }
        )
    if rows:
        st.subheader("Ajan bazında kullanım")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Sonuç")
    st.markdown(last["result"])
    with st.expander("Çalışma günlüğü"):
        for line in last["steps"]:
            st.write(line)


def render_history():
    traces = (
        sorted(RUNS_DIR.glob("*/trace.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if RUNS_DIR.exists()
        else []
    )
    if not traces:
        st.info("Henüz kayıt yok. Deney sekmesinden ilk deneyini çalıştır.")
        return
    sel = st.selectbox("Deney kaydı", [t.parent for t in traces], format_func=lambda p: p.name)
    summary_file = sel / "summary.json"
    if summary_file.exists():
        s = json.loads(summary_file.read_text(encoding="utf-8"))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Toplam çağrı", s.get("total_calls", 0))
        c2.metric("Toplam token", f"{s.get('total_tokens', 0):,}")
        c3.metric("Toplam ücret", cost_text(s))
        c4.metric("Geçen süre", f"{float(s.get('wall_time_s', 0)):.1f} sn")
        rows = [
            {
                "Ajan": name,
                "Model": ", ".join(v.get("models", [])),
                "Çağrı": v.get("calls", 0),
                "Input": v.get("prompt_tokens", 0),
                "Output": v.get("completion_tokens", 0),
                "Reasoning": v.get("reasoning_tokens", 0),
                "Toplam": v.get("total_tokens", 0),
                "Ücret ($)": round(float(v.get("cost_usd", 0)), 8),
                "Süre (sn)": round(float(v.get("latency_s", 0)), 1),
            }
            for name, v in s.get("agents", {}).items()
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
                "Input": ev.get("prompt_tokens", 0),
                "Output": ev.get("completion_tokens", 0),
                "Reasoning": ev.get("reasoning_tokens", 0),
                "Toplam": ev.get("total_tokens", 0),
                "Ücret ($)": ev.get("cost_usd"),
                "Süre (sn)": ev.get("latency_s", 0),
                "Çıktı": ev.get("output", "")[:200],
            }
        )
    if events:
        st.subheader("Çağrı bazında kullanım")
        st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
        with st.expander("Tam çıktılar"):
            for ev, raw_line in zip(events, (sel / "trace.jsonl").read_text(encoding="utf-8").splitlines()):
                raw = json.loads(raw_line)
                if raw.get("type") != "llm_call":
                    continue
                st.markdown(f"**{raw['agent']}** — `{raw['model']}`")
                st.text(raw.get("output", ""))


def main():
    st.title("LLM Araştırma Laboratuvarı")
    st.sidebar.header("Deney Ayarları")

    api_ok = bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    if not api_ok:
        st.sidebar.error("API anahtarı bulunamadı. `.env` içine OPENROUTER_API_KEY ekle.")

    model_ids, model_labels, catalog_error = model_catalog()
    if catalog_error:
        st.sidebar.warning(
            "Canlı OpenRouter model listesi alınamadı; fallback liste gösteriliyor. "
            "Manuel model ID alanına istediğin slug'ı yine yazabilirsin."
        )
    else:
        st.sidebar.caption(f"OpenRouter kataloğu: {len(model_ids)} text modeli")
    if st.sidebar.button("Model listesini yenile"):
        load_openrouter_catalog.clear()
        st.rerun()

    exp_name = st.sidebar.selectbox("Deney tipi", list(EXPERIMENTS))
    prompt, param, agents, optional, extras = build_sidebar(
        exp_name, model_ids, model_labels
    )

    tab_run, tab_hist = st.tabs(["Deney", "Geçmiş Kayıtlar"])

    with tab_run:
        if st.button(
            "Deneyi Çalıştır", type="primary", disabled=not api_ok, use_container_width=True
        ):
            if not prompt.strip():
                st.warning("Lütfen bir problem/konu gir.")
            else:
                data = execute(exp_name, prompt, param, agents, optional, extras)
                if data:
                    st.session_state["last"] = data
        last = st.session_state.get("last")
        if last and last["exp"] == exp_name:
            render_result(last)

    with tab_hist:
        render_history()


if __name__ == "__main__":
    main()
