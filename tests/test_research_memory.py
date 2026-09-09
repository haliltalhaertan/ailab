import json

import pytest

from lab.research_memory import build_memory, check_proposal


def put(root, *, status="OPEN", domain="integers", assumptions=None, lane_status="COMPLETED"):
    root.mkdir(parents=True)
    (root / "CLAIM_LEDGER.json").write_text(json.dumps({"claims": [{
        "claim_id": "C1", "statement": "x = x", "domain": domain,
        "assumptions": assumptions if assumptions is not None else ["x exists"],
        "status": status, "first_missing_step": "check boundary",
        "supporting_evidence": [{"lane_status": lane_status}],
    }]}), encoding="utf-8")


def test_exact_duplicates_merge_without_voting(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a)
    put(b)
    memory = build_memory([a, b, a])
    assert len(memory["records"]) == 1
    assert len(memory["records"][0]["observations"]) == 2
    assert memory["records"][0]["scientific_verdict"] == "NOT_ASSESSED"
    assert check_proposal(memory, " x = x ", "integers", ["x exists"])["match"] == "EXACT_BINDING_MATCH"
    assert check_proposal(memory, "x=x", "integers", ["x exists"])["match"] == "NO_EXACT_MATCH"


def test_domains_and_assumptions_distinct(tmp_path):
    a, b, c = [tmp_path / name for name in "abc"]
    put(a)
    put(b, domain="reals")
    put(c, assumptions=["x positive"])
    assert len(build_memory([a, b, c])["records"]) == 3


def test_conflicts_and_operational_failure_not_false(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a, status="PROVED")
    put(b, status="OPEN", lane_status="DROPPED")
    record = build_memory([a, b])["records"][0]
    assert record["disagreement"] is True
    assert record["reported_statuses"] == ["OPEN", "PROVED"]
    assert record["scientific_verdict"] == "NOT_ASSESSED"
    assert record["missing_steps"] == ["check boundary"]


def test_deterministic_provenance(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a)
    put(b)
    first = build_memory([a, b])
    assert first == build_memory([b, a])
    assert len(first["sources"][0]["sha256"]) == 64
    assert first["truth_verified"] is False


@pytest.mark.parametrize("reference", ["../secret.json", ".env", ".env.local", "private/context.json", "credentials.json", "API_SECRET.txt"])
def test_unsafe_evidence_rejected(tmp_path, reference):
    root = tmp_path / "run"
    put(root)
    path = root / "CLAIM_LEDGER.json"
    ledger = json.loads(path.read_text())
    ledger["claims"][0]["evidence_files"] = [reference]
    path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError):
        build_memory([root])


def test_missing_is_not_novel(tmp_path):
    memory = build_memory([tmp_path])
    assert memory["warnings"]
    result = check_proposal(memory, "x=x", None, None)
    assert result["novelty_assessed"] is False
    assert result["binding_complete"] is False


def test_source_cap(tmp_path, monkeypatch):
    put(tmp_path / "run")
    monkeypatch.setattr("lab.research_memory.MAX_SOURCE_BYTES", 10)
    with pytest.raises(ValueError, match="byte limit"):
        build_memory([tmp_path / "run"])


@pytest.mark.parametrize("field,value", [("status", []), ("evidence_files", None), ("supporting_evidence", "bad"), ("supporting_evidence", [{"evidence_files": None}])])
def test_invalid_claim_shape(tmp_path, field, value):
    put(tmp_path / "run")
    path = tmp_path / "run" / "CLAIM_LEDGER.json"
    ledger = json.loads(path.read_text())
    ledger["claims"][0][field] = value
    path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError):
        build_memory([tmp_path / "run"])


def test_invalid_ledger_shape(tmp_path):
    (tmp_path / "CLAIM_LEDGER.json").write_text("[]")
    with pytest.raises(ValueError, match="object"):
        build_memory([tmp_path])


def test_total_claim_cap(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    put(a)
    put(b)
    monkeypatch.setattr("lab.research_memory.MAX_CLAIMS", 1)
    with pytest.raises(ValueError, match="total claim"):
        build_memory([a, b])
