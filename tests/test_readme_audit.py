from __future__ import annotations

import json
from pathlib import Path

from lab.ui_model import load_default_agent_profile


def test_readme_states_audit_security_and_evidence_boundaries():
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "tek yürütme güvenlik sınırı container izolasyonudur" in text
    assert "best-effort defense-in-depth" in text
    assert "REFUTATION_CANDIDATE" in text
    assert "STALE_RUNNING" in text
    assert "-DwarningAsError=true" in text
    assert "claim hash" in text.lower()
    assert "run.lock" in text
    assert "mutable run işlemlerinden önce alınır" in text
    assert "Docker bulunmuyorsa `skip`" in text
    assert "global read-time seal uygulanmaz" in text
    assert "opsiyonel tam-state integrity maddesi bilinçli olarak kapsam dışında" in text
    assert "integrity_theorem_lab" not in text
    assert "experiment_method" in text


def test_ui_model_defaults_are_loaded_from_production_profile():
    root = Path(__file__).resolve().parents[1]
    app_text = (root / "app.py").read_text(encoding="utf-8")
    profile_path = root / "experiments" / "baseline_production_agents.json"
    expected = json.loads(profile_path.read_text(encoding="utf-8"))
    loaded = load_default_agent_profile(profile_path)

    assert "anthropic/claude-3.5-sonnet" not in app_text
    assert "deepseek/deepseek-r1" not in app_text
    assert "ROLE_MODELS =" not in app_text
    assert "load_default_agent_profile" in app_text
    assert loaded["agents"] == expected["agents"]
    assert loaded["orchestrator_default"] == expected["orchestrator_default"]
    assert loaded["agents"]["Theorist"]["model"] == "deepseek/deepseek-v4-pro"
    assert loaded["agents"]["AdversarialCritic"]["model"] == "moonshotai/kimi-k2.5"
    assert loaded["agents"]["VerificationEngineer"]["model"] == "google/gemini-3.7-flash"
    assert loaded["agents"]["IndependentAuditor"]["model"] == "google/gemini-3.7-flash"
    assert loaded["orchestrator_default"]["model"] == "z-ai/glm-5.3-flash"
