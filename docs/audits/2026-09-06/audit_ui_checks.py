"""Headless UI audit with temporary project data and a fake model catalog."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import lab.openrouter_catalog as catalog
from lab.project_manager import ProjectManager


REPO = Path(__file__).resolve().parents[3]
PAGES = ["app.py", *[f"pages/{p.name}" for p in sorted((REPO / "pages").glob("*.py"))]]


@pytest.mark.parametrize("has_project", [False, True])
@pytest.mark.parametrize("page", PAGES)
def test_pages_open_without_exceptions(tmp_path, monkeypatch, page, has_project):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(catalog, "fetch_openrouter_models", lambda *a, **kw: [
        catalog.OpenRouterModel(id="audit/fake", name="Audit fake model"),
    ])
    st.cache_data.clear()
    if has_project:
        ProjectManager().create_project(title="Audit project", problem="Audit problem", project_id="audit")
    at = AppTest.from_file(str(REPO / "app.py"), default_timeout=20).run()
    assert not at.exception
    if page != "app.py":
        at.switch_page(page).run()
    assert not at.exception, [entry.message for entry in at.exception]
