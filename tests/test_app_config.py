import app


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
