import json
from pathlib import Path

import streamlit as st

RUNS_DIR = Path("runs")

st.set_page_config(page_title="Ham Loglar", layout="wide")
st.title("Ham Loglar")
st.caption(
    "trace.jsonl içine yazılan tüm eventleri canlı ve filtrelenmeden izler. "
    "Provider gizli reasoning metnini API'de vermiyorsa o içerik alınamaz; "
    "provider'ın döndürdüğü reasoning/reasoning_details ise aynen görünür."
)


def runs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return sorted(
        [p.parent for p in RUNS_DIR.glob("*/trace.jsonl")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def read_trace(path: Path) -> tuple[str, list[dict]]:
    if not path.exists():
        return "", []
    raw = path.read_text(encoding="utf-8")
    events = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
            events.append(value if isinstance(value, dict) else {"raw": line})
        except json.JSONDecodeError:
            events.append({"type": "INVALID_JSON", "raw": line})
    return raw, events


all_runs = runs()
if not all_runs:
    st.info("Henüz run kaydı yok. Ana sayfadan araştırmayı başlat.")
    st.stop()

auto_latest = st.toggle("En yeni run'ı otomatik takip et", value=True)
show_all = st.toggle("Tüm logu göster", value=True)
show_json_events = st.toggle("Eventleri tek tek açılabilir JSON olarak da göster", value=False)
last_n = 200
if not show_all:
    last_n = int(st.number_input("Son kaç event?", 10, 10000, 200, 10))

selected: Path | None = None
if not auto_latest:
    selected = st.selectbox("Run", all_runs, format_func=lambda p: p.name)


@st.fragment(run_every=1.0)
def live_view() -> None:
    current = runs()
    if not current:
        st.info("Run bekleniyor...")
        return

    run = current[0] if auto_latest else selected
    if run is None:
        return

    trace_path = run / "trace.jsonl"
    raw, events = read_trace(trace_path)
    visible = events if show_all else events[-last_n:]
    visible_raw = raw if show_all else "\n".join(
        json.dumps(ev, ensure_ascii=False) for ev in visible
    )

    size = trace_path.stat().st_size if trace_path.exists() else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Run", run.name)
    c2.metric("Event", f"{len(events):,}")
    c3.metric("Log boyutu", f"{size / 1024:.1f} KB")

    st.text_area(
        "Canlı trace.jsonl — her satır tek JSON eventidir",
        value=visible_raw,
        height=720,
        disabled=True,
        key=f"raw_{run.name}_{len(events)}_{show_all}_{last_n}",
    )

    if show_json_events and visible:
        st.subheader("Tam Event JSON'ları")
        start = 1 if show_all else len(events) - len(visible) + 1
        for idx, event in enumerate(visible, start):
            label = f"#{idx} · {event.get('type', '?')}"
            if event.get("agent"):
                label += f" · {event['agent']}"
            request = event.get("request", {}) or {}
            tool = event.get("tool") or request.get("tool")
            if tool:
                label += f" · {tool}"
            with st.expander(label, expanded=False):
                st.json(event)

    st.download_button(
        "trace.jsonl indir",
        data=raw.encode("utf-8"),
        file_name=f"{run.name}_trace.jsonl",
        mime="application/x-ndjson",
        use_container_width=True,
    )


live_view()
