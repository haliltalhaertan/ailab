from pathlib import Path


def test_raw_logs_page_compiles():
    page = Path(__file__).resolve().parents[1] / "pages" / "2_Ham_Loglar.py"
    source = page.read_text(encoding="utf-8")
    compile(source, str(page), "exec")


def test_raw_logs_page_keeps_full_trace_controls():
    page = Path(__file__).resolve().parents[1] / "pages" / "2_Ham_Loglar.py"
    source = page.read_text(encoding="utf-8")
    assert "Tüm logu göster" in source
    assert "run_every=1.0" in source
    assert "trace.jsonl" in source
