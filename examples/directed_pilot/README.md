# Four-lane native pilot

`TASK_CONTRACT.json` is a complete sealed-input directed contract. Its parent
commit was read from the local llm-lab repository on 2026-09-07:
`db2d862fcdbd06c3dfac6bdac191bf9b7017717a`.
This is a repository HEAD identity, not a claim that the working tree is clean.
The coordinator separately records the directed execution module hashes.

Use this directory as `input_root`, the matching repository as `parent_repo`,
and a separate pilot state directory as `root`. The task project ID is
`directed-native-pilot`; it does not address the existing Collatz research state.
Three independent-input native lanes run in parallel. The final audit waits for
all three and receives only their selected public artifacts.

No lane uses a model or network. Current budget schema requires positive
ceilings, so the contract has a one-call nominal ceiling; `model` is forbidden,
all four execution types are fixed `symbolic_algebra`, and expected actual
calls/cost are zero. This pilot measures deterministic algorithm cross-checks,
not independent model performance. See `../pilot_README.md` for proof-label and
independence limitations.

Expected final audit results:

| Claim | Status |
| --- | --- |
| PILOT-A0 | PROVED |
| PILOT-A1 | PROVED |
| PILOT-GENERAL-MODULUS | PROVED |
| PILOT-EQUALITY | PROVED |
| PILOT-XUB | OPEN |

The primary ledger entry deliberately keeps the excluded XUB implication OPEN.
Overall completion is `COMPLETED_WITH_OPEN_CLAIMS`; it must not be interpreted as
XUB success. A complete package includes exact certificates, proof texts,
capability report, input hashes and a self-contained verifier.

`tests/test_directed_pilot.py` exercises orchestration with a rejecting provider,
a three-lane barrier to detect accidental serialization, exact claim statuses,
and package verification. Its synthetic parent-commit lookup is explicitly
mocked; the separate local acceptance run also verified the real repository
HEAD without mocking it. No model discovery or new Collatz result is claimed.
