# Directed task implementation and local acceptance — 2026-09-07

## Delivery

Canonical checkout: `C:\Users\MDP\Documents\Default Project\llm-lab`. Parent Git HEAD: `db2d862fcdbd06c3dfac6bdac191bf9b7017717a`. Working tree contains the earlier authorized audit/experiment changes and this implementation; no commit or push was made. The integration backup is `runs/before-directed-integration-20260907-1700`. `.env` was not opened, modified or included in any delivery. No real model/provider API calls were made for acceptance. Existing Collatz canonical research files were not modified.

The new mode accepts a principal-authored, versioned `DirectedTaskContract`; it runs the explicitly supplied dependency graph and returns a verifiable package. It does not create follow-up tasks. It supports the public validate/preflight/run/status/stop/resume/verify/package API and CLI, an isolated worker dispatch branch and a Streamlit page.

## Changed and new files

New core modules: `lab/directed.py`, `directed_contract.py`, `directed_gate.py`, `directed_budget.py`, `directed_provider.py`, `directed_engine.py`, `directed_algebra.py`, `directed_package.py`.

Integration changes: `lab/worker.py`, `lab/worker_launcher.py`, `tests/test_architecture.py`, `pyproject.toml` (explicit Pydantic 2 dependency), `.gitignore` (local batch/submission artifacts).

New UI/docs: `pages/7_Directed_Task.py`, `docs/directed_task.md`, this report. Examples: `examples/directed_exact_template.json`, `examples/directed_pilot/`, `examples/directed_container/`, `examples/directed_review/`, plus the individual pilot recipe explanations.

New test groups: `test_directed.py`, `test_directed_algebra.py`, `test_directed_package.py`, `test_directed_resume_guard.py`, `test_directed_engine_guards.py`, `test_directed_provider.py`, `test_directed_pilot.py`, `test_directed_examples.py`, `test_directed_ui.py`.

## Implemented controls

- Required claim definitions/domain, scope, parent commit, inputs/manifests, tool/method restrictions, model configuration, finite ceilings and result policy. Unknown fields, duplicate identities and cyclic dependencies are rejected.
- Raw contract bytes and input hashes frozen; same task ID cannot be reused with different contract bytes. HMAC-bound execution context includes capability-report digest and complete local Python execution inventory.
- Input and manifest integrity preflight; exact sealed snapshot membership checked before lane dispatch and finalization. Missing tools/configuration block execution before any model call.
- Up to four concurrent lanes; dependents wait for predecessors. Project state belongs to one coordinator. Explicit per-lane input/dependency views exclude producer reasoning and system prompts from auditors.
- Atomic conservative reservations before every provider attempt; cumulative signed budget persists across resume. Auth errors do not retry; transient retries are bounded. Missing or corrupted budget/cache/context prevents paid work.
- Per-run stop flag works even while queued; provider-visible content, reasoning, reasoning_content and structured reasoning_details are retained, including interrupted output. Credentials are screened/redacted structurally.
- Completed cached lanes do not repeat on resume. Finalization failures retain a resumable error; a completed state is written only after package build and required-output validation.
- Scientific claim labels remain distinct from execution status. Model agreement, successful program exit and finite tests do not automatically certify the natural-language primary claim. Native pilot proof labels are restricted to the exact supplied recipe.
- Delivery includes frozen inputs, claims, readable findings, exact evidence, provider-visible output when present, provenance, budgets, environment versions, per-lane/computation hashes, trusted standalone verifier and deterministic ZIP. Unexpected ideas are recorded as escalation candidates only.

## Test evidence

1. Full canonical test suite: **479 passed**, 210.41 seconds. Evidence: `runs/directed-final-tests-20260907.xml`.
2. After the final provider-field compatibility, recipe type narrowing and computation-manifest changes: all directed, UI and architecture tests rerun: **104 passed**, 33.63 seconds. Evidence: `runs/directed-targeted-tests-20260907.xml`. These counts overlap and must not be added.
3. Ruff: all checks passed for lab/pages/tests. Mypy: no issues in all 46 lab source files.
4. Actual Streamlit page opened at http://127.0.0.1:8501/Directed_Task. The free-pilot load button is present. AppTests cover loading, editing invalidation, preflight, launch, resume, verification and worker dispatch without dotenv.
5. Deliberately duplicated ZIP-member negative test emits one expected standard-library warning; the adversarial archive is correctly rejected.

Negative tests cover scope/schema rejection, concurrent reservations, snapshots modified during execution, coordinated context/contract rewriting, missing signatures/budgets, archive alterations, unfinished finalization, missing promised outputs, dependency failures, overclaim/underclaim, 401/no retry, 429/bounded retry, interrupted stream preservation and no duplicate completed calls.

## Local pilot

Run: `directed-962caecc25fa4c4db63496bb0e3add02`. Status: `COMPLETED_WITH_OPEN_CLAIMS`. Four lanes completed. Actual provider calls: zero; cost: zero. Recorded work duration: 0.503 seconds (not total CLI startup time).

Two distinct native coefficient algorithms derive the small-b identity, boundary checks cover equality and near-integer behavior, and an audit compares the three public evidence objects. All use the same codebase; this is an exact deterministic infrastructure acceptance test, not an independent LLM discovery experiment.

| Claim | Final audit label |
|---|---|
| PILOT-A0 | PROVED |
| PILOT-A1 | PROVED |
| PILOT-GENERAL-MODULUS | PROVED |
| PILOT-EQUALITY | PROVED |
| PILOT-XUB | OPEN |

The definitions preserve beta=log(3)/log(2)-1 and q(x)=exp(2*pi*i*2^x). Lacunary powers 1,2,4,8,16 are not misclassified as an ordinary constant-ratio geometric progression. The pointwise strict contraction does not supply a uniform contraction constant. Pair-state multiplier is exactly 2^(b+2)/9.

PROVED here denotes the documented elementary algebra and cross-checked exact certificates. No formal kernel verified these proofs; XUB, E6-N2, E7-B4, crossing estimates and Collatz remain outside the pilot conclusion.

Package: `C:\Users\MDP\Documents\Default Project\llm-lab\research_state\directed-native-pilot\directed\directed-962caecc25fa4c4db63496bb0e3add02\package\COMPLETE_PACKAGE.zip`

SHA256: `c95446b4ee2b7fc1c1734521d2f97594e6f00c5697d47f3e74c9bbb0d5111685`

Package verified, repeated packaging produced identical bytes, and a terminal resume returned the same package without executing lanes again. A separate real background-worker launch also completed four lanes successfully.

## Container and parallelism acceptance

The declared Docker Python example completed its bounded integer assertions and explicit AILAB_TEST report. Run `directed-cb7865fd2c3a4029b456ad34e4a5a802`; package SHA256 `32813d99ee59b7d2c4185f7f6f36a07168cb214021e2175df5605873d786a148`. Program execution alone did not promote the primary claim to a general theorem.

Four fake provider calls, each with a one-second delay:

| Worker limit | Peak concurrent calls | Provider interval | Full orchestration duration |
|---|---:|---:|---:|
| 4 | 4 | 1.211s | 2.280s |
| 2 | 2 | 2.361s | 3.197s |

These are measured synthetic-provider intervals; they do not predict a remote model's response speed. External API calls were zero. Raw acceptance evidence: `runs/directed-acceptance-20260907/ACCEPTANCE.json`.

## Practical boundaries

- Native symbolic execution supports the frozen small-b pilot recipe only. Generic symbolic workloads can be supplied as declared isolated Python jobs; their successful execution is not a proof certificate.
- Lean adapter is unavailable in this version and is always blocked; installing Lean alone does not enable an adapter.
- Real provider quality, pricing and model support were not benchmarked. Model/price ceilings in the review example require actual principal configuration. Prices are declared ceilings, not a live billing guarantee; unexpected reported usage stops further dispatch. In-flight calls may finish before a stop is observed; reservations remain charged conservatively when usage is uncertain.
- Model calls and container jobs overlap. CPU-heavy native Python threads are subject to Python's execution constraints; the separate batch mode uses process workers for partitioned numerical scans.
- ZIP byte integrity does not establish mathematical truth or publisher authenticity. Keep the external ZIP hash when handing off a package; private HMAC material is intentionally not exported.
- An external principal must invoke the public API/CLI with a contract. No autonomous bridge was installed inside the Collatz research repository.

## Use

Open the Directed Task page; select **Ücretsiz küçük-b pilotunu yükle**, then **Sözleşmeyi doğrula ve planı göster**, and **Sözleşmeyi dondur ve çalıştır**. Loading the example performs no execution. Use the results section to verify/download the package. The principal API and exact CLI commands are documented in `docs/directed_task.md`.
