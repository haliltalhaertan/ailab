# Problem packs

`problem_packs/<pack>/` contains checked-in, human-reviewed domain code that can be executed as deterministic research tooling without importing domain logic into `lab/`.

Each pack directory must contain a `README.md`. That README must document:

- which script emits which AILab evidence kind;
- which independent checker or validator verifies the candidate/search output;
- the exact finite space, stopping rule, or other basis for every claim described as exhaustive.

Pack scripts are invoked through `ScriptTool` by a path relative to the trusted `problem_packs/` root, for example `tropical_circuit/check_candidate.py`. There is intentionally no `manifest.json`, registry, plugin loader, or dynamic import mechanism.

Search and checking are separate trust paths. A pack's `search*.py` and `check*.py` scripts must not import each other and must not share pack-local helper modules such as `common.py` or `*_semantics.py`. If both need the same semantics, duplicate the small trusted definition or validate it independently; shared implementation would defeat the independent-checker boundary.

`lab/` must never import `problem_packs`. The engine treats pack scripts as checked-in evidence producers, not as core Python dependencies. Generated code continues to use the separate guarded `code_experiment` path and does not gain trusted-pack privileges.
