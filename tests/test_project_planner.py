from types import SimpleNamespace

from lab.project_planner import ProjectDraft, generate_project_draft, parse_project_draft


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


def test_project_planner_repairs_raw_latex_backslashes_and_trailing_commas():
    # This mirrors the failure mode from prompts containing \Omega / \omega / \Theta.
    raw = r'''Here is the requested JSON:
    {
      "title": "Tropical Circuit Bounds",
      "project_id": "tropical-circuit-bounds",
      "description": "Gap from \Omega(n^2) to O(n^3)",
      "experiment": "Teorem Araştırması",
      "problem": "Try to prove \omega(n^2) or find an o(n^3) construction with \Theta notation.",
      "literature_query": "tropical circuit reachability lower bound",
      "tags": ["tropical", "circuits",],
    }
    Extra explanation that should be ignored.'''
    draft = parse_project_draft(raw, "fallback")
    assert draft.project_id == "tropical-circuit-bounds"
    assert "\\Omega(n^2)" in draft.description
    assert "\\omega(n^2)" in draft.problem
    assert draft.tags == ["tropical", "circuits"]


def test_project_planner_repairs_literal_newline_inside_json_string():
    raw = '{"title":"X","problem":"line one\nline two","tags":[]}'
    draft = parse_project_draft(raw, "fallback")
    assert draft.problem == "line one\nline two"


def test_generate_project_draft_retries_once_when_output_has_no_json():
    class FakeAgent:
        name = "ProjectPlanner"
        model = "fake/model"
        temperature = 0.2

        def __init__(self):
            self.calls = 0

        def respond(self, messages):
            self.calls += 1
            if self.calls == 1:
                content = "I cannot format that right now."
            else:
                content = '{"title":"Recovered","project_id":"recovered","problem":"P","experiment":"Teorem Araştırması","literature_query":"q","tags":["math"]}'
            return content, SimpleNamespace(model=self.model, content=content)

    agent = FakeAgent()
    draft, _, messages = generate_project_draft("research this", agent)  # type: ignore[arg-type]
    assert agent.calls == 2
    assert draft.project_id == "recovered"
    assert "GEÇERSİZ ÇIKTI" in messages[0]["content"]
