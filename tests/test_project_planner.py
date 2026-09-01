from lab.project_planner import ProjectDraft, parse_project_draft


def test_project_planner_parses_clean_json():
    raw = '''{
      "title": "Tropical Circuit Lower Bounds",
      "project_id": "tropical-circuit-lower-bounds",
      "description": "Tropical circuit complexity araştırması.",
      "experiment": "Teorem Araştırması",
      "problem": "K_n üzerinde lower bound hedefini literatürle doğrula ve daralt.",
      "literature_query": "tropical circuit reachability provenance lower bound",
      "tags": ["math", "tropical", "circuits"]
    }'''
    draft = parse_project_draft(raw, "fallback")
    assert isinstance(draft, ProjectDraft)
    assert draft.project_id == "tropical-circuit-lower-bounds"
    assert draft.experiment == "Teorem Araştırması"
    assert draft.tags == ["math", "tropical", "circuits"]


def test_project_planner_accepts_code_fence_and_normalizes_slug():
    raw = '''```json
    {
      "title": "Şema Eşleme Araştırması",
      "project_id": "Şema Eşleme Araştırması",
      "description": "test",
      "experiment": "bilinmeyen mod",
      "problem": "problem",
      "literature_query": "schema mapping open problems",
      "tags": "Database, AI, database"
    }
    ```'''
    draft = parse_project_draft(raw, "fallback")
    assert draft.project_id == "sema-esleme-arastirmasi"
    assert draft.experiment == "Teorem Araştırması"
    assert draft.tags == ["database", "ai"]


def test_project_planner_falls_back_to_user_prompt_for_missing_problem():
    raw = '{"title":"X","tags":[]}'
    draft = parse_project_draft(raw, "Benim araştırma problemim")
    assert draft.problem == "Benim araştırma problemim"
