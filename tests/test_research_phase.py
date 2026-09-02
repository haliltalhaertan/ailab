from __future__ import annotations

import json
from pathlib import Path

from lab.research_contract import ResearchContract
from lab.run_controller import RunController
from lab.runtime_health import normalize_runtime
from lab.trace import Trace


def _contract(policy: str = "REQUIRED") -> ResearchContract:
    return ResearchContract.from_dict(
        {
            "problem": "P",
            "object_model": "Finite objects.",
            "validity_definition": "Checked by frozen semantics.",
            "equivalence_definition": "Literal equality.",
            "objective": {"type": "compute", "measure": "value"},
            "pilot_policy": policy,
            "open_targets": [{"id": "T1", "statement": "Compute T1", "target_type": "COMPUTE"}],
            "evidence_policy": {"numerical": "OPEN"},
        }
    )


def test_phase_and_execution_status_are_independent(tmp_path: Path):
    trace = Trace("phase", out_dir=tmp_path / "runs")
    controller = RunController(tmp_path / "state", trace)

    controller.set_runtime(status="RUNNING", research_phase="PILOT")
    controller.set_runtime(status="PAUSED_ERROR")
    current = controller.runtime()

    assert current["status"] == "PAUSED_ERROR"
    assert current["research_phase"] == "PILOT"
    controller.set_research_phase("PROOF")
    current = controller.runtime()
    assert current["status"] == "PAUSED_ERROR"
    assert current["research_phase"] == "PROOF"
    trace.close()


def test_legacy_runtime_defaults_to_literature_without_rewrite(tmp_path: Path):
    root = tmp_path / "state"
    root.mkdir()
    path = root / "runtime.json"
    raw = {"status": "STOPPED", "completed_iterations": 1}
    path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    before = path.read_bytes()

    normalized = normalize_runtime(root)

    assert normalized["research_phase"] == "LITERATURE"
    assert path.read_bytes() == before


def test_runtime_health_preserves_existing_phase(tmp_path: Path):
    normalized = normalize_runtime(
        tmp_path,
        {"status": "STOPPED", "research_phase": "FALSIFICATION"},
    )
    assert normalized["status"] == "STOPPED"
    assert normalized["research_phase"] == "FALSIFICATION"


def test_contract_draft_and_freeze_drive_phase(tmp_path: Path):
    root = tmp_path / "state"
    contract = _contract("REQUIRED")
    contract.save(root)
    assert json.loads((root / "runtime.json").read_text(encoding="utf-8"))["research_phase"] == "FORMALIZATION"

    contract.freeze(root, frozen_problem="P")
    assert json.loads((root / "runtime.json").read_text(encoding="utf-8"))["research_phase"] == "PILOT"

    # A later frozen-contract ledger save must not rewind a progressed phase.
    runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    runtime["research_phase"] = "PROOF"
    (root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    contract.save(root)
    assert json.loads((root / "runtime.json").read_text(encoding="utf-8"))["research_phase"] == "PROOF"


def test_not_applicable_freeze_skips_pilot_phase(tmp_path: Path):
    root = tmp_path / "state"
    contract = _contract("NOT_APPLICABLE")
    contract.save(root)
    contract.freeze(root, frozen_problem="P")
    assert json.loads((root / "runtime.json").read_text(encoding="utf-8"))["research_phase"] == "DISCOVERY"
