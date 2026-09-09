# Offline candidate archive

Inspired by ShinkaEvolve's use of explicit evaluators and candidate archives, this module evaluates already submitted answers. It does not generate candidates, run their code, call providers, or launch follow-up research. The evaluator is the existing `lab.directed_eval.evaluate` public API; its six fixed task definitions are available through `python -m lab.directed_eval list`.

Run `python -m lab.research_candidates examples/research_candidates.json --output candidate_archive.json`.

Python/UI interface: `from lab.research_candidates import evaluate_candidates`; call `evaluate_candidates(payload)` and serialize its returned dictionary. Invalid input raises `ValueError`. Uploads are limited to 2 MB and 256 candidates.

Payload has exactly `suite_id` (`research-assistant-small-v1`) and `candidates`. Each candidate supplies `candidate_id`, `task_id`, `answer` object, and `model`; optional fields are `parent_id`, `reasoning_effort`, `usage`, and `elapsed_seconds`. Usage follows directed_eval (calls, prompt_tokens, completion_tokens, reasoning_tokens, cost_usd, cost_complete). IDs must be unique; parents must exist in this upload and concern the same task. Cycles are rejected. Lineage is submitted provenance, not verified independence.

Output `candidates` is ordered by ID and preserves answer, parent, unverified usage/model provenance, status and fingerprint. Exact finite task checks give `EXACT_PASS`/`FAIL` with `exact_score` 1/0. Prose receives `REVIEW_REQUIRED` (or `FAIL` for empty analysis), always with a null exact score. A plausible or confident proof is never automatically promoted.

SHA-256 over canonical task ID plus answer identifies byte-equivalent structured answers; duplicate evaluation is reused and `duplicate_of` identifies the lexicographically first ID. This is exact JSON deduplication, not semantic equivalence checking. Every duplicate retains its own provenance, and duplicates are not independent votes. `rankings_by_task` contains unique exact passes only; all passes tie and sort by ID. It does not rank mathematical quality, novelty, or prose truth. Usage is self-reported and does not influence ranking.

The output is deterministic for the same candidate set, regardless of input ordering. No timestamp or random ID is added. This archive provides finite benchmark evidence, not formal proof or discovery certification.
