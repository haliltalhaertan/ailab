# Completed-experiment hardening — 2026-09-07

Applied to the canonical checkout at `C:\Users\MDP\Documents\Default Project\llm-lab`.

## Changes

- Python execution reads the bounded raw stdout artifact for an optional `AILAB_TEST={"status":"PASS","checked_points":123}` report. FAIL, INCONCLUSIVE, malformed, vacuous or duplicate reports reject the run even when its process exits zero. A generated PASS remains an untrusted script report, not independently validated mathematics. Legacy scripts without a report remain execution-only.
- Proposer and coding-agent instructions require assertions around Boolean tests and an explicit report after checks succeed. No `sys` import is needed for failure propagation.
- Research agenda retirement is separate from evidence status. A manager KILL sets `agenda_status=RETIRED`; verified evidence remains intact. Prompt context also recognizes legacy manager KILL records as retired without rewriting the historical ledger.
- Manager instructions explicitly allow KILL with COMPUTATION_PASS, distinguish finite predicate evaluation from proof checking, require preserving requested scope and identifying unfinished ranges, and ask for concise decisions.
- Generated code experiments cannot obtain PROOF_CANDIDATE merely from positive model reviews.

## Limits

This is not a formal proof engine or a guarantee against incorrect generated tests. No prose-based mathematical verdict is extracted from stdout. Optional script reports cannot establish truth or close a research target. Arbitrary scripts without assertions or reports may still execute successfully; their execution remains separate from claim verification. Scope coverage across multiple checks still needs a dedicated verified aggregation mechanism. Concise prompts do not guarantee provider latency or reasoning length. No model settings or historical results were changed, and no new paid experiment was started.

Original source files were backed up in `runs/before-postrun-hardening-20260907-154102` before installation, after checking their SHA-256 hashes against the inspected versions.

## Validation
- Full canonical test suite: 368 passed in 133.15 seconds; no skips reported.
- Ruff: all checks passed for modified code and new tests.
- Mypy: no issues in 37 source files.
- New regression coverage: 11 cases, including zero-exit failure, malformed/vacuous/duplicate reports, assertion failure, no proof promotion, and legacy/new agenda retirement.

