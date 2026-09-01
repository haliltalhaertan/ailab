from pathlib import Path


def test_projects_page_compiles():
    path = Path(__file__).resolve().parents[1] / "pages" / "1_Projeler.py"
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
