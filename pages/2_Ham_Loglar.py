from __future__ import annotations

import gzip
import json
from pathlib import Path

import streamlit as st

from lab.ui_model import read_run_index

RUNS_DIR = Path("runs")
st.set_page_config(page_title="Ham Loglar", layout="wide")
st.title("Ham Loglar")
st.caption(
    "Ana olaylar `trace.jsonl`, yüksek hacimli reasoning/content stream'i canlı run'da `stream.jsonl`, "
    "tamamlanan run'da `stream.jsonl.gz` dosyasındadır. Sayfa her saniye canlı dosyayı baştan okumaz; "
    "yalnız yeni byte'ları tail eder."
)


def indexed_runs() -> list[Path]:
    rows = read_run_index(RUNS_DIR)
    result = []
    for row in rows:
        path = Path(str(row.get("run_dir") or ""))
        if (path / "trace.jsonl").exists():
            result.append(path)
    if result:
        return result
    return sorted(
        [p.parent for p in RUNS_DIR.glob("*/trace.jsonl")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if RUNS_DIR.exists() else []


def _resolved_log_path(path: Path) -> Path:
    if path.exists():
        return path
    gz_path = Path(str(path) + ".gz")
    return gz_path if gz_path.exists() else path


def tail_file(path: Path, state_key: str) -> tuple[str, list[dict]]:
    actual = _resolved_log_path(path)
    state = st.session_state.setdefault(state_key, {"path": "", "offset": 0, "raw": "", "events": []})
    if state.get("path") != str(actual):
        state.clear()
        state.update({"path": str(actual), "offset": 0, "raw": "", "events": []})
    if not actual.exists():
        return state["raw"], state["events"]

    if actual.suffix == ".gz":
        # Completed stream archives are immutable. Decompress once per selected
        # run and keep the decoded events in session_state rather than inflating
        # a multi-megabyte gzip file on every one-second fragment refresh.
        if int(state.get("offset", 0)) > 0:
            return state["raw"], state["events"]
        with gzip.open(actual, "rb") as handle:
            payload = handle.read()
        size = len(payload)
        chunk = payload
        state["offset"] = size
    else:
        size = actual.stat().st_size
        if int(state.get("offset", 0)) > size:
            state.update({"offset": 0, "raw": "", "events": []})
        with actual.open("rb") as handle:
            handle.seek(int(state.get("offset", 0)))
            chunk = handle.read()
            state["offset"] = handle.tell()

    if chunk:
        text = chunk.decode("utf-8", errors="replace")
        state["raw"] += text
        for line in text.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                value = {"type": "INVALID_JSON", "raw": line}
            state["events"].append(value if isinstance(value, dict) else {"raw": line})
    return state["raw"], state["events"]


all_runs = indexed_runs()
if not all_runs:
    st.info("Henüz run kaydı yok.")
    st.stop()

auto_latest = st.toggle("En yeni run'ı otomatik takip et", value=True)
selected = None if auto_latest else st.selectbox("Run", all_runs, format_func=lambda p: p.name)
show_all = st.toggle("Tüm logu göster", value=False)
last_n = int(st.number_input("Son kaç event?", 10, 5000, 250, 10)) if not show_all else 0


@st.fragment(run_every=1.0)
def live_view() -> None:
    current = indexed_runs()
    if not current:
        return
    run = current[0] if auto_latest else selected
    if run is None:
        return
    trace_path = run / "trace.jsonl"
    stream_path = _resolved_log_path(run / "stream.jsonl")
    trace_raw, trace_events = tail_file(trace_path, "raw_trace_tail")
    stream_raw, stream_events = tail_file(run / "stream.jsonl", "raw_stream_tail")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Run", run.name)
    c2.metric("Core event", f"{len(trace_events):,}")
    c3.metric("Stream batch", f"{len(stream_events):,}")
    total_size = sum(p.stat().st_size for p in (trace_path, stream_path) if p.exists())
    c4.metric("Toplam log", f"{total_size / 1024:.1f} KB")

    t1, t2 = st.tabs(["Core trace", "Stream"])
    with t1:
        visible = trace_events if show_all else trace_events[-last_n:]
        visible_raw = trace_raw if show_all else "\n".join(json.dumps(ev, ensure_ascii=False) for ev in visible)
        st.text_area("trace.jsonl", value=visible_raw, height=620, disabled=True, key=f"trace_view_{run.name}_{len(trace_events)}_{show_all}")
        st.download_button(
            "trace.jsonl indir",
            data=trace_raw.encode("utf-8"),
            file_name=f"{run.name}_trace.jsonl",
            mime="application/x-ndjson",
            width="stretch",
        )
    with t2:
        visible = stream_events if show_all else stream_events[-last_n:]
        visible_raw = stream_raw if show_all else "\n".join(json.dumps(ev, ensure_ascii=False) for ev in visible)
        st.caption(f"Kaynak: `{stream_path.name}`")
        st.text_area("stream.jsonl", value=visible_raw, height=620, disabled=True, key=f"stream_view_{run.name}_{len(stream_events)}_{show_all}")
        st.download_button(
            "stream.jsonl indir",
            data=stream_raw.encode("utf-8"),
            file_name=f"{run.name}_stream.jsonl",
            mime="application/x-ndjson",
            width="stretch",
        )


live_view()
