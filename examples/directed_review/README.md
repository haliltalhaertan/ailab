# Model review DAG template

This is an unexecuted template, not a ready-configured or benchmarked model run.
Replace **every** `provider/model-id` with a model available through your provider.
Choose supported reasoning settings and verify the input/output price ceilings
before freezing. The illustrative 1/2 USD-per-million prices are not current
provider prices; the 0.30 USD total ceiling is merely an example budget.

Two producer lanes have no dependencies and cannot read each other's outputs.
The auditor waits for both and receives selected public artifacts, not producer
reasoning or system prompts. Role separation is not a guarantee of mathematical
independence. The claim remains subject to the scientific status validator;
agreement among models cannot manufacture a proof certificate.

Use this folder as the input root and llm-lab as the parent repository. Review
the pinned parent commit. Configure credentials in the invoking process environment;
directed mode does not read `.env`. Schema validation and preflight do not send
model calls. Starting the configured task does; nothing here was auto-executed.
No autonomous literature retrieval, scope expansion or follow-up task is allowed.
