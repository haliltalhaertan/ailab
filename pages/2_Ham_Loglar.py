import json
from pathlib import Path

import pandas as pd
import streamlit as st

RUNS_DIR = Path("runs")

st.set_page_config(page_title="Ham Loglar", layout="wide")
st.title("Ham Loglar")
st.caption(
    "trace.jsonl içine yazılan her event burada filtrelenmeden görülebilir. "
    "Provider gizli reasoning metnini API'de vermiyorsa o içerik loglarda bulunamaz; "
    "provider tarafından döndürülen reasoning/reasoning_details ise aynen kaydedilir."
)


def available_runs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        [p.parent for p in RUNS_DIR.glob("*/trace.jsonl")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def read_raw(path: Path) -> tuple[str, list[dict]]:
    if not path.exists():
        return "", []
    raw = path.read_text(encoding="utf-8")
    events: list[dict] = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except json.JSONDecodeError:
            events.append({"type": "INVALID_JSON", "raw": line})
    return raw, events


runs = available_runs()
if not runs:
    st.info("Henüz run kaydı yok. Ana sayfadan bir araştırma başlat.")
    st.stop()

auto_latest = st.toggle("En yeni çalışmayı canlı takip et", value=True)
show_all = st.toggle("Tüm ham logu göster", value=True)
last_n = 200
if not show_all:
    last_n = st.number_input("Son kaç event?", min_value=10, max_value=10000, value=200, step=10)

selected_run: Path | None = None
if not auto_latest:
    selected_run = st.selectbox(
        "Run",
        runs,
        format_func=lambda p: p.name,
    )


@st.fragment(run_every=1.0)
def live_log_view() -> None:
    current_runs = available_runs()
    if not current_runs:
        st.info("Run bekleniyor...")
        return

    run = current_runs[0] if auto_latest else selected_run
    if run is None:
        st.info("Bir run seç.")
        return

    trace_path = run / "trace.jsonl"
    raw, events = read_raw(trace_path)

    visible_events = events if show_all else events[-int(last_n):]
    if show_all:
        visible_raw = raw
    else:
        visible_raw = "\n".join(json.dumps(ev, ensure_ascii=False) for ev in visible_events)
        if visible_raw:
            visible_raw += "\n"

    size_bytes = trace_path.stat().st_size if trace_path.exists() else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Run", run.name)
    c2.metric("Event", f"{len(events):,}")
    c3.metric("Dosya", f"{size_bytes / 1024:.1f} KB")
    c4.metric("Görüntülenen", f"{len(visible_events):,}")

    st.subheader("Canlı ham trace.jsonl")
    st.text_area(
        "Her satır tek bir JSON eventidir",
        value=visible_raw,
        height=650,
        disabled=True,
        key=f"raw_{run.name}_{len(events)}_{show_all}_{last_n}",
    )

    if events:
        rows = []
        for idx, ev in enumerate(events, 1):
            rows.append(
                {
                    "#": idx,
                    "ts": ev.get("ts", ""),
                    "type": ev.get("type", ""),
                    "agent": ev.get("agent", ""),
                    "model": ev.get("model", ""),
                    "tool": ev.get("tool", "") or (ev.get("request", {}) or {}).get("tool", ""),
                    "status": ev.get("status", "") or ev.get("new_status", ""),
                    "tokens": ev.get("total_tokens", ""),
                    "cost_usd": ev.get("cost_usd", ""),
                    "latency_s": ev.get("latency_s", ""),
                }
            )
        st.subheader("Event indeksi")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        with st.expander("Eventleri tek tek tam JSON olarak aç", expanded=False):
            for idx, ev in enumerate(visible_events, 1 if show_all else len(events) - len(visible_events) + 1):
                label = f"#{idx} · {ev.get('type', '?')}"
                if ev.get("agent"):
                    label += f" · {ev['agent']}"
                if ev.get("tool"):
                    label += f" · {ev['tool']}"
                with st.expander(label, expanded=False):
                    st.json(ev)

    st.download_button(
        "trace.jsonl dosyasını indir",
        data=raw.encode("utf-8"),
        file_name=f"{run.name}_trace.jsonl",
        mime="application/x-ndjson",
        use_container_width=True,
    )


live_log_view()
