# Exact research memory

Inspired by structured research knowledge in systems such as Kosmos, this local module groups prior claim receipts with their source hashes. It reads only explicitly selected run folders (or extracted package folders), makes no model calls, and does not alter runs.

```powershell
.venv\Scripts\python.exe -m lab.research_memory export --run C:\research\run-one --run C:\research\run-two --output C:\research\memory-new.json
```

The output must not exist. This prevents accidental replacement of frozen artifacts. The Python API is `build_memory(run_folders: list[Path]) -> dict`; proposal screening is `check_proposal(memory, statement, domain, assumptions) -> dict`.

An exact fingerprint binds statement, domain and assumptions/definitions. Normalization trims outer string whitespace and sorts object keys; mathematical case, punctuation, internal whitespace and list ordering remain significant. Different domains or assumptions never merge. Unknown bindings remain explicitly incomplete. No exact match does **not** establish novelty, and this tool neither recognizes paraphrases nor searches the literature.

Every classification is reported evidence. Different classifications produce a disagreement flag without voting or choosing a winner. Lane failure or dropping is an operational fact, never an inferred counterexample. Supporting lane receipts sharing definitions and code are not independent mathematical proofs. All records keep source SHA-256, claim IDs, missing steps and evidence references; missing evidence remains visibly missing. Hashing establishes captured byte identity, not truth or authenticity.

Sources are capped at 8 MB per file and 10,000 claims per ledger. Symlinks, environment files and escaping evidence paths are rejected. Reads are snapshots: use completed runs for stable, repeatable exports. This is advisory memory, not a formal proof database or a replacement for the principal researcher's judgment.
