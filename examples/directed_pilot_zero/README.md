# Explicit zero-model-budget native pilot

This is a new task, `small-b-native-pilot-zero-v2`, with project ID
`directed-native-pilot-zero`. Its four recipe inputs retain exactly the hashes
and bytes of the previous native pilot. The existing sealed v1 contract has not
been edited; v2 has distinct contract bytes and a distinct task identity.

All model call, token and cost ceilings are explicitly zero. The four native
lanes are the two exact coefficient derivations, the boundary checker and the
dependent final auditor. No model calls, external services or provider
credentials are required. Wall-time and concurrency budgets remain positive.

Model-containing contracts must retain strictly positive model budgets. The
capability gate reports effective model call, token and cost ceilings of zero
for every entirely native plan, including the older nominal-budget v1 contract.

The same proof-label and scientific limits apply as in
`../directed_pilot/README.md`: four elementary identities are PROVED with explicit
arguments and native exact certificates; XUB stays OPEN, no formal kernel or
independent model discovery is claimed, and no continuation task is launched.
