from pathlib import Path


def test_deprecated_use_container_width_is_absent():
    root = Path(__file__).resolve().parents[1]
    sources = [root / "app.py", *sorted((root / "pages").glob("*.py"))]
    offenders = [str(path.relative_to(root)) for path in sources if "use_container_width" in path.read_text(encoding="utf-8")]
    assert offenders == []


def test_streamlit_minimum_supports_width_api():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"streamlit>=1.47,<2"' in pyproject
