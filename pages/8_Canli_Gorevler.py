"""Unified read-only monitor; never dispatches a provider call or resumes a run."""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
from lab.task_workbench_ui import render_queue, render_delivery_and_review, render_run_controls

from lab.directed_monitor import (
    discover_runs, heartbeat_warning, lane_names, lane_snapshot, load_roots,
    read_json, register_root, filter_runs, status_label, ACTIVE, contained,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "research_state" / "monitor_roots.json"

st.set_page_config(page_title="Canlı görevler", layout="wide")
st.title("Çalışmalar")
st.caption("Baş araştırmacı görevleri · Canlı durum, reasoning ve sonuçlar")

with st.sidebar.expander("Kayıt klasörleri"):
    st.text(str(ROOT / "research_state"))
    with st.form("monitor_register"):
        extra_path = st.text_input("Başka bir deney kayıt klasörü", placeholder="C:/.../runs")
        add = st.form_submit_button("İzlemeye ekle")
    if add:
        try:
            register_root(CONFIG, extra_path)
            st.success("Klasör izleme listesine kaydedildi.")
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    try:
        registered = load_roots(CONFIG)
        for path in registered:
            st.text(str(path))
            if not path.is_dir():
                st.warning("Bu kayıt klasörü şu anda bulunamıyor.")
    except (OSError, ValueError) as exc:
        registered = []
        st.warning(f"Kayıt klasörü listesi okunamadı: {exc}")

roots = list(dict.fromkeys([ROOT / "research_state", *registered]))
render_queue(ROOT)
with st.container(horizontal=True, vertical_alignment="center"):
    search = st.text_input('Görev ara', placeholder='Görev ara…', key='live_search', label_visibility='collapsed')
    refresh_requested = st.button("Görev listesini yenile", icon=":material/refresh:")
    with st.popover("Filtreler", icon=":material/tune:"):
        follow = st.toggle("Canlı yenile · 2 saniye", value=True)
        active_only = st.checkbox('Yalnız çalışan olarak kayıtlı görevler', key='live_active_only')



@st.cache_data(ttl=2, max_entries=16)
def discover(paths: tuple[str, ...]):
    return discover_runs([Path(x) for x in paths])


@st.cache_data(max_entries=32, show_spinner=False)
def read_lane(root: str, folder: str, lane: str, signature: tuple):
    return lane_snapshot(Path(root), Path(folder), lane)


if refresh_requested:
    discover.clear()
    read_lane.clear()
initial_runs = discover(tuple(str(x) for x in roots))
has_active = any(row['runtime'].get('status') in ACTIVE for row in initial_runs)
if not has_active:
    st.caption("Çalışan görev yok; otomatik yenileme beklemede. Yeni görevler için listeyi yenileyin.")


@st.fragment(run_every=2 if follow and has_active else None)
def monitor():
    all_runs = discover(tuple(str(x) for x in roots))
    if has_active and not any(row['runtime'].get('status') in ACTIVE for row in all_runs):
        st.rerun()
    runs = filter_runs(all_runs, search, active_only)
    if not runs:
        st.info('Aramanıza uygun görev bulunamadı. Aramayı temizleyin veya çalışan görev filtresini kaldırın.' if all_runs
                else 'Henüz görev kaydı bulunamadı. İzlenen kayıt klasörleri bölümünden deneyin runs klasörünü ekleyebilirsiniz.')
        return

    if len(runs) == 1000:
        st.warning("Liste 1.000 görevle sınırlı; daha dar kayıt klasörleri seçin.")
    options = {row["folder"]: row for row in runs}
    selected = st.selectbox(
        "İzlenecek görev", list(options),
        format_func=lambda key: f'{options[key]["project_id"]} · {status_label(options[key]["runtime"].get("status"))} · {options[key]["run_id"][-8:]}',
        key="live_selected_run",
    )
    row = options[selected]
    root, folder = Path(row["root"]), Path(row["folder"])
    state_key = "live_last_runtime"
    try:
        runtime = read_json(root, folder / "runtime.json")
        st.session_state[state_key] = (selected, runtime)
    except (OSError, ValueError) as exc:
        previous = st.session_state.get(state_key, (None, {}))
        runtime = previous[1] if previous[0] == selected else {}
        st.warning(f"Durum dosyası henüz okunamıyor; varsa son okunmuş kayıt gösteriliyor. {exc}")

    warning = heartbeat_warning(runtime)
    if warning:
        st.warning(warning)
    usage = runtime.get("usage", {})
    if not isinstance(usage, dict):
        usage = {}
    with st.container(horizontal=True):
        st.metric("Durum", status_label(runtime.get("status")))
        st.metric("Aktif kol", len(runtime.get("active_lanes") or {}))
        st.metric("Sonuçlanan kol", len(runtime.get("completed_lanes") or []))
        st.metric("Başlatılan çağrı", usage.get("calls", "—"))
        known_cost = usage.get("provider_cost_usd")
        st.metric("Bildirilen maliyet", f'${known_cost:.6f}' if isinstance(known_cost, (int, float)) else "Henüz bilinmiyor")
    if usage.get("provider_cost_complete") is not True:
        upper = usage.get("provider_cost_upper_bound_usd")
        st.warning("Toplam ücret henüz kesin değil. Bildirilmemiş kullanım sıfır maliyet değildir."
                   + (f" Rezervasyonlara dayanan üst sınır: ${upper:.6f}." if isinstance(upper, (int, float)) else ""))
    st.caption(f'Son kayıt: {runtime.get("heartbeat_at", runtime.get("updated_at", "bilinmiyor"))}')
    try:
        names = lane_names(root, folder, runtime)
    except (OSError, ValueError) as exc:
        st.warning(str(exc))
        return
    if not names:
        st.info("Çalışma kollarının kayıtları henüz oluşmadı.")
        return
    active = runtime.get("active_lanes") or {}
    completed = runtime.get("lanes") or {}
    done = len([n for n in names if n in completed])
    st.progress(done / len(names), text=f'{len(names)} kolun {done} tanesi sonuçlandı. Bu gösterge bilimsel başarı oranı değildir.')
    lane = st.selectbox("Reasoning'i okunacak kol", names, key=f"live_lane_{selected}")
    try:
        signature = []
        for relative in ("TASK_CONTRACT.json", f"lanes/{lane}/PARTIAL.json", f"lanes/{lane}/RESPONSE.json", f"lanes/{lane}/RESULT.json"):
            candidate = folder / relative
            if candidate.exists():
                stat = contained(root, candidate).stat()
                signature.append((relative, stat.st_mtime_ns, stat.st_size))
        snapshot = read_lane(str(root), str(folder), lane, tuple(signature))
        st.session_state["live_last_lane"] = (selected, lane, snapshot)
    except (OSError, ValueError) as exc:
        previous = st.session_state.get("live_last_lane", (None, None, {}))
        snapshot = previous[2] if previous[:2] == (selected, lane) else {}
        st.warning(f"Akış kaydı henüz okunamıyor; varsa son okunmuş metin gösteriliyor. {exc}")
    reasoning, answer, result = st.tabs(["Reasoning", "Cevap", "Sonuç ve ayrıntılar"], key="live_content_tabs", on_change="rerun")
    for tab, field, label in ((reasoning, "reasoning", "Reasoning"), (answer, "content", "Cevap")):
        if not tab.open:
            continue
        with tab:
            value = str(snapshot.get(field) or "")
            if value:
                st.caption(f"Toplam {len(value):,} karakter · Son {min(len(value), 12000):,} karakter gösteriliyor")
                st.code(value[-12_000:], language=None, wrap_lines=True)
                st.download_button(f"{label} tam metnini indir", value, file_name=f"{lane}-{field}.txt", mime="text/plain", key=f"download_{field}_{selected}_{lane}")
            else:
                st.info("Sağlayıcıdan bu alanda metin henüz gelmedi. Bazı modeller görünür reasoning göndermeyebilir.")
    if result.open:
        result_data = snapshot.get('result') or {}
        if result_data.get('first_missing_step'):
            st.info('Açık kalan adım: ' + str(result_data['first_missing_step']))
        if result_data.get('public_findings'):
            st.text(str(result_data['public_findings']))
        elif not result_data:
            st.info('Bu kol için nihai sonuç henüz oluşmadı.')
        with st.expander('Teknik kayıtlar'):
            st.json(result_data, expanded=False)
            st.code(json.dumps(snapshot.get("reasoning_details", []), ensure_ascii=False, indent=2)[-12_000:], language="json", wrap_lines=True)
        st.caption("Tamamlanmış cevap bilimsel doğruluk onayı değildir. Paket doğrulayıcı dosya bütünlüğünü denetler; matematiksel hakikati doğrulamaz.")
        render_delivery_and_review(ROOT, folder, lane)
        if st.button('Hazır deney paketini getir', key=f'package-{selected}'):
            try:
                package = contained(root, folder / 'package/COMPLETE_PACKAGE.zip')
                if package.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError('Paket 64 MB üzerinde; dosya konumundan alın.')
                st.session_state['package_download'] = (selected, package.read_bytes())
            except (OSError, ValueError) as exc:
                st.warning(f'Paket henüz hazır değil veya okunamıyor: {exc}')
        package_saved = st.session_state.get('package_download')
        if package_saved and package_saved[0] == selected:
            st.download_button('Deney paketini indir', package_saved[1], 'COMPLETE_PACKAGE.zip', 'application/zip')
    with st.expander('Tüm çalışma kollarının durumu'):
        st.dataframe([{"Kol": n, "Durum": status_label(completed.get(n, "RUNNING" if n in active else "PENDING"))} for n in names], hide_index=True)
    with st.expander('Görev kimliği ve dosya konumu'):
        st.text(f'Görev: {runtime.get("task_id", "")}\nProje: {row["project_id"]}\nÇalışma: {row["run_id"]}\nKlasör: {folder}')
    render_run_controls(folder, runtime)
    if st.button("Araştırma teslim özetini hazırla", key=f"report_{selected}"):
        try:
            from lab.directed_reports import render_research_report
            from lab.research_review import build_reviewed_report
            report = build_reviewed_report(folder, ROOT / 'research_state/research_reviews.json')
            report_text = render_research_report(report)
            st.session_state["live_report"] = (selected, report_text, report)
        except (OSError, ValueError) as exc:
            st.warning(f"Teslim özeti henüz üretilemedi: {exc}")
    saved_report = st.session_state.get("live_report")
    if saved_report and saved_report[0] == selected:
        st.caption("Teslim özeti düğmeye basıldığı andaki kaydı gösterir; yeni sonuçlar için yeniden hazırlayın.")
        st.text(saved_report[1])
        st.download_button("Teslim özetini indir", saved_report[1], file_name="RESEARCH_REPORT.md", mime="text/markdown")
        if saved_report[2].get("model_metrics"):
            st.dataframe(saved_report[2]["model_metrics"], hide_index=True)


monitor()
