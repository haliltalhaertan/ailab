# Exact small-b directed pilot

The four `pilot_*.json` files are explicit source inputs for `symbolic_algebra`
lanes. Each file must appear in the task's sealed input manifest with its actual
SHA-256 hash. Producers `derive_a`, `derive_b`, and `boundary` have no dependencies
and must not receive each other's outputs. The `audit` lane declares all three
dependencies and receives their complete public native certificates. The runner
must supply dependency provenance; algebra code alone cannot authenticate files.

The exact definitions are beta=log(3)/log(2)-1,
q(x)=exp(2*pi*i*2^x), z=2^(x-beta)>0 and the supplied A_b operator.
The input definition and claim strings are deliberately closed and versioned:
changing them rejects the recipe instead of certifying a different theorem.
The alpha decimal approximation never defines a constant here.

## What is actually independent

The first route enumerates unordered frequency pairs and records their signed
differences. The second separately constructs a dense polynomial, reverses its
coefficients and multiplies the two arrays. Their Laurent coefficients agree
exactly for b=0..4. They do not share a coefficient-derivation helper. They share
one codebase, exact input definitions, standard algebra and the accompanying
reviewed proof texts. This is algorithmic cross-checking, not independent model
discovery or an independent implementation maintained by another team. Any pilot
requiring two model agents to independently discover proofs must run those model
lanes separately; this offline pilot does not claim to have done so.

## Proof labels and scope

`PROVED` here denotes the explicitly stated elementary mathematical argument,
with declared standard real/complex identities, plus the bounded exact integer
coefficient checks. It is not `FORMALLY_VERIFIED`, not an LLM vote, and not a
numerical sample promoted to proof. The general finite-sum identity and equality
case have explicit arguments for arbitrary finite b, while machine coefficient
enumeration covers only b=0..4. No arbitrary supplied formula can inherit these
labels. Per-claim statements and proof texts must accompany exported statuses.

The boundary certificate records b=0, positive integer equality points, exact
z=1/2 squared moduli, and an explicit sequence approaching integers showing the
absence of a uniform contraction bound on all noninteger z. State multipliers
are exactly 4/9, 8/9, 16/9, 32/9 and 64/9. The exponential frequencies are
lacunary, not a constant-ratio geometric series.

PILOT-XUB stays OPEN. No occupation/crossing estimate, E6-N2, E7-B4, asymptotic
profile, polynomial lower bound, novelty claim or Collatz theorem follows from
this certificate. The pilot does not launch any continuation; new research
observations require ESCALATION_CANDIDATE review by the principal researcher.

The offline tests cover recipe mutation, forged and missing audit dependencies,
duplicate producer certificates, forbidden dependency visibility, exact boundary
values and deterministic outputs. No API calls or project research data are used.
