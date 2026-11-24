# LLM Lab

A multi-agent LLM research lab with verification and persistent research memory. It works with any OpenAI-compatible API provider (OpenRouter by default).

The repository supports two kinds of work:

1. **General multi-agent experiments:** pipelines, research loops, debates, and panels.
2. **Theorem Research Lab:** a manager, theorist, adversarial critic, verifier, literature scout, and independent auditor for open problems in mathematics and theoretical computer science. It includes a persistent conjecture ledger, deterministic computation tools, literature screening, checkpoints, freezing, and stop/resume controls.

<a name="kurulum"></a>
## Setup

```powershell
cd ailab
python -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item .env.example .env
```

To install the test tools:

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q
```

Add at least one API key to `.env`:

```text
OPENROUTER_API_KEY=...
```

The `LAB_*_MODEL` variables set defaults only. In the Streamlit interface, you can select a different OpenRouter model for each agent.

<a name="mimari"></a>
## Architecture

```text
Streamlit UI / Research Control
            |
            | worker_request.json (`experiment_method`)
            v
     Detached Worker
        /       \
       v         v
Orchestrator   TheoremResearchLab
                  /    |     \
             StepStore RunController ToolRegistry
                |          |          |
              SQLite    lock/stop   Python/Z3/Lean
                           |
                     ResearchState
                           |
                      Trace / Audit
```

All five experiment types (`theorem_lab`, `research_loop`, `debate`, `pipeline`, `panel`) use the same detached worker execution path, selected by `experiment_method`. There is no `execute_inline` path that runs the `Orchestrator` directly within Streamlit. The home page and Research Control share a live panel showing the active stage, agent/model/effort, reasoning and response streams, token and cost metrics, and a timeline. The `DURDUR` (Stop) request is passed to the worker through `stop.flag`.

The main theorem workflow is implemented in `lab/theorem_engine.py`. The `TheoremResearchLab` class is also exported from `lab/__init__.py`.

Key runtime files:

```text
research_state/<project_id>/
  project.json              # project metadata; READY/ARCHIVED
  problem_frozen.json
  state.json                # research ledger
  theorem_graph.json
  runtime.json              # single source of truth for runtime status
  run_config.json
  tool_availability.json    # declared/runtime/effective tool capability snapshot
  research_steps.sqlite3
  worker.json               # actual worker pid/run_id/launched_at
  worker_launch.json        # launcher pid and platform/breakaway details
  run.lock                  # only during an active run
  checkpoints/
  workspace/

runs/
  index.jsonl
  <run_id>/
    trace.jsonl
    stream.jsonl
    summary.json
```

## Theorem Research Lab

The goal is to use LLMs in a controlled, auditable research system, rather than as chatbots that generate proofs.

```text
                    Research Manager
                          |
          +---------------+---------------+
          |               |               |
       Theorist       Adversarial       Verifier
                          Critic            |
          |               |        deterministic tools
          +---------------+---------------+
                          |
                    Evidence Guard
                          |
                 Research State / Graph
                          |
                  Literature Scout
                          |
                 Independent Auditor
```

<a name="kanıt-merdiveni"></a>
### Evidence Ladder

One or more LLMs saying “it looks correct” does not make a claim `[PROVEN]`.

Research ledger statuses:

```text
OPEN
REFUTATION_CANDIDATE
COMPUTATION_PASS
PROOF_CANDIDATE
PROVEN
FAIL
KNOWN
BARRIER
DROPPED
```

The UI and runtime also use `STALE_RUNNING` to report worker health. It is not a mathematical evidence status. It appears when a saved `RUNNING` record cannot be confirmed by a live `run.lock` owner or a recent heartbeat. If the user explicitly chooses `Stale run'ı temizle` (Clear stale run), the stale lock is removed and the runtime becomes `INTERRUPTED`, preserving the step cache and partial work.

The central evidence guard compares the status requested by the LLM with the available evidence:

- `REFUTATION_CANDIDATE`: a counterexample suggested by an LLM but not deterministically verified. It does not permanently close the research question.
- `COMPUTATION_PASS`: requires meaningful, successful deterministic evidence. Z3 needs at least one assertion; the tropical grid needs at least one case to have actually been checked.
- `PROOF_CANDIDATE`: requires verifier and critic review; it is not a formal proof.
- `PROVEN`: requires successful Lean evidence bound to the same ledger item, iteration, and claim; clean source; an axiom audit; a verifier PASS; and no KILL from the critic.
- `FAIL`: only a deterministically verified counterexample can produce a permanent mathematical FAIL. An LLM's negative assessment alone does not qualify.

If the manager requests a stronger status than the evidence supports, the system automatically lowers it to a less certain status and records the change in the trace.

<a name="formal-doğrulama-yolu"></a>
### Formal Verification Path

An LLM can use the `lean_draft` tool to write a formal candidate to the project's `formal/candidates/` directory. The engine binds the candidate source to the current ledger item, iteration, and claim hash, and includes the file's SHA-256 hash in the verification chain.

The Lean source gate rejects `sorry`, `admit`, `axiom`, `opaque`, `native_decide`, `set_option`, `partial def`, and other defined escape-hatch patterns. The checker runs with `-DwarningAsError=true`. Output suggesting the presence of `sorry` or an unexpected axiom is not accepted as a success. The result of `#print axioms` is checked against the allowed axiom set.

A `returncode == 0` is therefore not enough for `[PROVEN]`. The claim hash, item and iteration, theorem name and type, source SHA, source cleanliness, and axiom checks must agree within the same evidence chain. If Lean is not installed or host execution of Lean has not been explicitly enabled, formal verification fails or remains inconclusive; an LLM opinion cannot substitute for it.

<a name="durdur--devam-bütünlüğü"></a>
## Stop/Resume Integrity

All experiment types run in a separate worker process, not in the Streamlit render thread. The worker can continue after the browser closes. A STOP request from the home page or Research Control reaches the worker through `stop.flag`. Only the theorem workflow resumes at individual steps. Other workflows rerun the same request as a new run.

The project lock `run.lock` is acquired before operations that modify a run. Without the lock, the worker does not change `run_config.json`, a stale stop flag, the worker identity, or runtime status. If a second worker cannot acquire the lock, it leaves the active worker's files alone and can only write a separate busy diagnostic.

The Windows launcher first tries `CREATE_BREAKAWAY_FROM_JOB`. If the parent job rejects it, the launcher falls back to less restrictive detached flags and records `breakaway=false` in `worker_launch.json`. Breakaway is not guaranteed on every Windows setup.

A heartbeat updates `runtime.json`. For a RUNNING state, if the lock is missing or no longer held by a live process, or the heartbeat is over 120 seconds old, the system can identify `STALE_RUNNING` and offer a safe way to resume.

Completed steps are cached in the SQLite `StepStore` using content fingerprints. The completed payloads are sealed with HMAC and are not reused if the seal fails verification. Partial provider-visible work is saved with `reasoning`, `reasoning_details`, and `content`.

At the start of each iteration, the ledger context, ledger revision, and next task are frozen. If the proposer returns a different claim after resuming, the system does not silently attach new evidence to the old conjecture; the run fails closed to `PAUSED_ERROR`.

This is not provider KV-cache resumption. If the API does not expose private inference state, only provider-visible reasoning and content can be reused safely.

<a name="araştırma-statei-bütünlük-sınırı"></a>
## Research State Integrity Limits

`PROVEN` evidence has an HMAC proof seal, and the current SHA-256 hash of the linked Lean file is rechecked. Completed StepStore cache entries also undergo HMAC verification. Setting `LAB_EVIDENCE_HMAC_KEY` externally lets you keep the key outside the project data. Without it, the StepStore HMAC key is stored in the project directory. This mode detects accidental or simple manual edits; it is not a security signature against a hostile model with access to the project files.

There is **no global read-time seal over the canonical items and events in the entire `state.json` file**. The optional full-state integrity item in the audit was deliberately left out of scope. There is no general tamper-proof ledger guarantee against an administrator who can modify the project filesystem and the local HMAC key. Security and evidence claims are limited to the narrower PROVEN and cache checks described above.

<a name="codeexperimentagent-güvenlik-modeli"></a>
## CodeExperimentAgent Security Model

LLM-generated Python **does not run in the host Python process**. CodeExperimentAgent requires Docker or Podman to run experiments. If neither container runtime is available, the experiment **fails closed** rather than running code on the host.

The container starts with these security limits:

```text
network = none
root filesystem = read-only
capabilities = drop ALL
no-new-privileges
RAM limit
PID limit
CPU limit
timeout
stdout/stderr byte limit
only the project workspace is mounted read-write
```

AST validation is a **best-effort defense-in-depth** preflight check, not a security sandbox. It rejects known risky import, attribute, dunder, string, and call patterns early, but the Python AST policy is not assumed to cover every possible bypass. Container isolation is the only execution security boundary for generated code.

The agent's workspace tools:

```text
write_file
read_file
list_files
patch_file
run_python
finish
```

`finish` is accepted only when there is evidence of at least one successful `run_python` execution. A failed final run cannot close the experiment as a success. The evidence manifest records the SHA-256 hashes of the script, stdout and stderr, along with the return code, duration, and run identity.

A computational test does not automatically yield `[PROVEN]`, regardless of its size. Its evidence level remains `COMPUTATION_ONLY`.

## Literature / novelty screening

`LiteratureClient` searches arXiv and Crossref for candidate publications. It does not turn a long query into a single exact phrase. Instead, it tries several more robust query variants.

The system treats a search with no results as:

```text
INCONCLUSIVE
```

“I didn't find a result” never proves that a result is new. A critical novelty claim needs an independent literature audit.

## Structured output

The theorem pipeline fails closed when parsing structured LLM output:

1. Parse the JSON directly.
2. Try deterministic repairs for common JSON and LaTeX escape problems.
3. If needed, make a single LLM call to repair the format only.
4. If parsing still fails, do not silently use a default. Set the run to `PAUSED_ERROR`.

## Deterministic tools

`ToolRegistry` is the single source for both the tool schemas exposed to the theorem prompt and actual dispatch.

The theorem worker measures executable capabilities at startup and stores three snapshots: `declared_tool_availability` is the initial set; `runtime_tool_availability` is what is currently available on the machine; and `effective_tool_availability` is their safe intersection. If a tool disappears before a resume, the effective set shrinks. A newly installed tool cannot silently expand the same `STOPPED`, `PAUSED_ERROR`, or `INTERRUPTED` run. To use a new capability, start a new run. The snapshot appears in `tool_availability.json`, the trace, and `run_config.json`. Research Control also displays the effective status as Lean, Z3, Script, Tropical, and Container badges.

Lean is exposed in the LLM's tool schema only when `LAB_ALLOW_HOST_LEAN=1` and `lean` or `lake` is on PATH. The worker separately checks the Z3 installation, trusted script roots, and the container runtime. Unavailable tools are removed from the prompt and fail closed at dispatch. An unavailable tool, timeout, syntax or formatting problem, or infrastructure error is not a mathematical `FAIL`. The verifier should treat it as `INCONCLUSIVE`.

- **ScriptTool:** runs only reviewed, trusted scripts under `research_tools/` and `problem_packs/`. In a legacy project with no contract, a checked-in script that exits successfully (exit code 0) can count as `NUMERICAL_PASS` without a structured evidence trailer. A project with a frozen contract fails closed if that trailer or its binding is missing.
- **Z3Tool:** treats SMT-LIB input with no assertions as `inconclusive / no assertions`. A real assertion is required for sat/unsat computation evidence.
- **TropicalGridTool:** tests min-plus shortest-path **function equality** on a chosen finite grid of nonnegative weights. `GRID_PASS` is not proof of formal or monomial-level equality with the simple-path provenance polynomial. `gate_count` counts only internal non-edge gates; `edge_gate_count` is reported separately.
- **LeanTool:** checks a formal proof bound to the claim.
- **CodeExperimentAgent:** runs the experimental code loop inside a container.

<a name="araştırma-statei-ve-eşzamanlılık"></a>
## Research State and Concurrency

Two theorem workers cannot run on the same project at the same time. `run.lock` is an atomic project lock. If a second process cannot acquire it, it stops without modifying the active run's mutable state or configuration files.

`runtime.json` is the authoritative source for run status. `project.json.status` is used only for project metadata states `READY` and `ARCHIVED`. `worker.json` contains the actual worker identity, not the run status.

Run directories use a unique `run_id` based on a microsecond timestamp and a UUID. In addition to the human-readable `project_id`, there is an immutable `project_uuid`. If a project is deleted and recreated with the same ID, the old run history is not associated with the new project.

<a name="trace--log-ölçeklenmesi"></a>
## Trace and Log Scaling

LLM call metadata, tool results, state changes, and checkpoints are written to `trace.jsonl`. High-volume streaming deltas are buffered separately in `stream.jsonl`.

The Raw Logs page tails files by byte offset instead of rereading them from the start on every refresh. `runs/index.jsonl` indexes the history of projects and runs.

For each LLM call, the following fields are recorded when available:

```text
agent
model
prompt_tokens
completion_tokens
reasoning_tokens
cached_tokens
total_tokens
cost_usd
latency_s
requested reasoning effort
```

`summary.json` stores run totals.

<a name="web-arayüzü"></a>
## Web Interface

```powershell
.venv\Scripts\streamlit run app.py
```

Use ProjectPlanner on the `Projeler` (Projects) page to create a new project from a single prompt. When an experiment starts, the UI writes a worker request with `experiment_method` and starts the detached worker. All five experiment types appear in the same live panel and can be stopped with `DURDUR` (Stop). There is no `execute_inline` execution path. In theorem research, Research Control can resume a run at the step level. For other experiments, rerunning starts a new run.

On Windows, CodeExperimentAgent requires Docker Desktop or a compatible Podman installation. Without a container runtime, text-based theorem research can still run, but the generated-code experiment step fails for safety.

## CLI

Example:

```powershell
.venv\Scripts\python experiments\theorem_research.py "Let P_n be the simple s-t path provenance polynomial of K_n over the min-plus tropical semiring. Improve either the O(n^3) upper bound or the Omega(n^2) lower bound." tropical-circuit 6
```

Running again with the same `project_id` preserves the frozen problem and persistent state.

<a name="ci--kalite-kapısı"></a>
## CI and Quality Gate

GitHub Actions runs these checks:

```text
pytest: Python 3.10
pytest: Python 3.11
pytest: Python 3.12
pytest: Python 3.13
Windows Python 3.14: pytest + Ruff + mypy
Ruff
mypy
container-integration
```

The standard pytest suite skips the behavioral test that requires a container runtime when Docker is unavailable. The separate `container-integration` GitHub Actions job runs on an Ubuntu runner with Docker and checks actual container behavior: network isolation, a read-only root filesystem, a workspace output directory as the only writable area, and container cleanup after a timeout. AST unit tests also check the best-effort policy but do not replace the container security boundary.

The latest audit-hardening changes are not merged into `main` until the entire quality gate is green.

The goal is to rule out wrong hypotheses as early as possible and to keep every strong claim at the level its evidence supports, rather than producing lots of convincing prose.
