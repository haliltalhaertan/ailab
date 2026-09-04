from lab import TheoremResearchLab, Trace
from lab.theorem_engine import TheoremResearchLab as EngineLab
from lab.trace import Trace as CoreTrace


def test_completion_recovery_and_trace_are_core_classes():
    assert TheoremResearchLab is EngineLab
    assert Trace is CoreTrace
    assert TheoremResearchLab.__module__ == "lab.theorem_engine"
    assert Trace.__module__ == "lab.trace"


def test_truncated_retry_stage_label():
    assert Trace._theorem_stage_label(
        "iter:2:proposer:truncated_retry", "Theorist"
    ) == "Tur 2 · Theorist · öneri (kesilme sonrası tekrar)"
