import importlib.util
from pathlib import Path

import pytest


def load_app_module():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("ailab_streamlit_app", app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return load_app_module()


def test_theorem_research_is_available_in_ui(app):
    exp = app.EXPERIMENTS["Teorem Araştırması"]
    assert exp["method"] == "theorem_lab"
    assert exp["roles"] == [
        "ResearchManager",
        "Theorist",
        "AdversarialCritic",
        "VerificationEngineer",
        "LiteratureScout",
        "IndependentAuditor",
    ]


def test_theorem_roles_have_independent_default_models(app):
    for role in app.EXPERIMENTS["Teorem Araştırması"]["roles"]:
        assert app.default_model(role)
        assert role in app.ROLE_LIBRARY


def test_model_search_filters_slug_and_label_case_insensitive(app):
    ids = ["z-ai/glm-5.3-flash", "openai/gpt-4o", "moonshotai/kimi-k3"]
    labels = {
        "z-ai/glm-5.3-flash": "Z.ai: GLM 5.3 Flash",
        "openai/gpt-4o": "OpenAI: GPT-4o",
        "moonshotai/kimi-k3": "Moonshot: Kimi K3",
    }
    assert app.filter_models(ids, labels, "5.3") == ["z-ai/glm-5.3-flash"]
    assert app.filter_models(ids, labels, "KIMI") == ["moonshotai/kimi-k3"]
    assert app.filter_models(ids, labels, "") == ids


def test_every_experiment_explains_when_to_use_it(app):
    for experiment in app.EXPERIMENTS.values():
        assert experiment.get("description")


def test_tool_execution_error_is_not_labeled_counterexample(app):
    label, tone = app.tool_status(
        {"ok": False, "tool": "tropical_grid", "error": "grid checker için 2 ≤ n ≤ 7 gerekli"}
    )
    assert label == "HATA"
    assert tone == "error"


def test_real_counterexample_is_labeled_counterexample(app):
    label, tone = app.tool_status(
        {
            "ok": False,
            "tool": "tropical_grid",
            "error": "",
            "metadata": {"status": "COUNTEREXAMPLE"},
        }
    )
    assert label == "COUNTEREXAMPLE"
    assert tone == "error"


def test_compact_live_renderer_is_available(app):
    assert hasattr(app, "LiveTimelineRenderer")
