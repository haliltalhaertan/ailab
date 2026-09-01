from lab.reasoning_settings import (
    get_reasoning_effort,
    load_settings,
    normalize_effort,
    save_settings,
)


def test_reasoning_effort_aliases():
    assert normalize_effort(None) is None
    assert normalize_effort("Provider default") is None
    assert normalize_effort("low") == "low"
    assert normalize_effort("Medium") == "medium"
    assert normalize_effort("Max") == "xhigh"
    assert normalize_effort("xhigh") == "xhigh"
    assert normalize_effort("off") == "none"


def test_reasoning_settings_round_trip(tmp_path):
    path = tmp_path / "reasoning_settings.json"
    save_settings(
        {"agents": {"Theorist": "xhigh", "LiteratureScout": "low"}},
        path,
    )
    assert load_settings(path)["agents"]["Theorist"] == "xhigh"
    assert get_reasoning_effort("Theorist", path) == "xhigh"
    assert get_reasoning_effort("LiteratureScout", path) == "low"


def test_numbered_agent_falls_back_to_base_role(tmp_path):
    path = tmp_path / "reasoning_settings.json"
    save_settings({"agents": {"Panelist": "medium"}}, path)
    assert get_reasoning_effort("Panelist 2", path) == "medium"
