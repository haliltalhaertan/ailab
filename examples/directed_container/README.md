# Container acceptance example

Load `TASK_CONTRACT.json` in Directed Task, set the input root to this folder and
the parent repository to the llm-lab checkout. The contract pins the current
development commit; update it explicitly if the parent state changes.

This example makes no LLM calls. It requires an available Docker/Podman runtime
and the project's configured sandbox image. Preflight will report missing
capability. Provisioning an absent image can require a download; the contract
does not grant arbitrary host execution.

`experiment.py` checks integers 1 through 200 with exact integer arithmetic and
assertions, then emits one explicit `AILAB_TEST` PASS receipt. Its raw bytes are
SHA256-pinned. If edited, replace the declared digest and use a new task identity.
Container success is execution evidence, not independent certification of the
natural-language claim and not a proof over all integers. No experiment has been
automatically launched merely by creating this example.
