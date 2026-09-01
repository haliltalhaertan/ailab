import importlib.util
from pathlib import Path


def load_app_module():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("ailab_streamlit_app", app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load_app_module()


def test_theorem_research_is_available_in_ui():
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


def test_theorem_roles_have_independent_default_models():
    for role in app.EXPERIMENTS["Teorem Araştırması"]["roles"]:
        assert app.default_model(role)
        assert role in app.ROLE_LIBRARY


def test_model_search_filters_slug_and_label_case_insensitive():
    ids = ["z-ai/glm-5.3-flash", "openai/gpt-4o", "moonshotai/kimi-k3"]
    labels = {
        "z-ai/glm-5.3-flash": "Z.ai: GLM 5.3 Flash",
        "openai/gpt-4o": "OpenAI: GPT-4o",
        "moonshotai/kimi-k3": "Moonshot: Kimi K3",
    }
    assert app.filter_models(ids, labels, "5.3") == ["z-ai/glm-5.3-flash"]
    assert app.filter_models(ids, labels, "KIMI") == ["moonshotai/kimi-k3"]
    assert app.filter_models(ids, labels, "") == ids


def test_every_experiment_explains_when_to_use_it():
    for experiment in app.EXPERIMENTS.values():
        assert experiment.get("description")
