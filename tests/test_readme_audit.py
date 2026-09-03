from __future__ import annotations

from pathlib import Path


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
