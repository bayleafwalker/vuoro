# Bound proposal composition source qualification

Candidate Vuoro service **0.1.89** consumes the actual published Sprintctl
**0.15.1** wheel from source `c089e5fd2f1681236f227ffb9f4d0c751ff7d8e7`.
Its SHA256 is `519da8901d72f674f5789b0aad6392718bbc791c61cd77e6f53d78625e7928f7`;
the downloaded wheel's GitHub attestation binds it to that source, `v0.15.1`
and `release-sprintctl.yaml`. Owner admission landed in Sprintctl PR135;
its append-only native producer carrier landed in PR136.

The service adds the released `work.effect.propose-bound-v1` operation through
adapter composition. Auditctl0.1.9, adapter-kit0.2.0, schema-runtime0.1.0,
work schema21 and compatibility floors remain unchanged. Startup checks
compatibility without migrating. The resource-disabled work catalog has
**73** operations and metadata digest
`19e7d6b31cb72151958c0a3e7e3ec5af65c74531abfffdebade4458bf2770d3b`.
The composed work/audit catalog has **78** operations and revision
`ea95ae6f785c3af7dadf68743ffa4a4ff493ad7a8971015532dcb63060e0f60f`.
These are measured installed-artifact values.

## Qualification

The candidate service wheel and all four manifest-pinned released wheels were
installed into a separate Python3.12 environment. Dependency consistency and
work/audit/combined catalog gates pass. Intentionally incorrect work metadata
and combined catalog fingerprints both fail their respective gates.

All **52** configured conformance cases pass on disposable PostgreSQL16.15
with zero skips. The 13 new bound HTTP cases assert distribution versions and
module origins inside `site-packages`, preventing editable workspace imports
from satisfying this proof. CI builds a service wheel, installs the immutable
manifest dependencies in a separate environment, and runs those cases before
the later historical-owner downgrade.

The HTTP fixture uses the production invocation shell's in-process TestClient
and static identities. It registers an authenticated run, reserves through
native reserve, appends evidence, and explicitly inserts the precommitted
trailer relation. It does not prove trailer ingestion or deployed enrollment.
First admission must match the submitted causal basis, authenticated HTTP run
resolution, actual reservation ID and captured Release. Exact replay after
rejection, item editing, chain advance and reservation release retains the
original admission and returns the current intent state with only one effect.
Missing authority and every changed binding field refuse before effects;
missing reserve, wrong commit/Release/tail, envelope key, foreign repository,
legacy key collision, stale full revision and append-first histories are
checked with exact errors and effect/ledger counts.

The targeted source service/distribution suite passes **323** tests with
19 unconfigured PostgreSQL skips; the complete source workspace passes
**1600** with 26 such skips. These editable source tests have a distinct role
from the immutable composition proof. Configured current-owner resource
observations, accepted discovery/restart and ledger/effect/provider consumer
checks additionally pass **6 + 1 + 104** cases. Resource gap receipts retain
their nonqualifying classification. The local consumer invocation initially
combined packages whose `conftest` imports conflict; separate invocations
matching CI passed without a product change.

Independent Astra review approved this bounded source after the first-response
correlation assertions passed. Verification context/result and authored
knowledge are validated before landing; exact-head CI remains a landing gate.

## Remaining gates

Publication, wheel/image provenance, pull and exercise by exact image digest,
consumer GitOps repin and actual runtime convergence are separate receipts.
This source grants no production writer authority. Stable native caller
binding and the required capabilities must be independently established.

The complete S4 offline pipeline still requires actual authority outage and
restart, ordered reserve/Release/trailer/evidence/bound-proposal confirmation,
unchanged original captures, failure histories and effective-state equivalence.
Historical import, attribution, hook cutover and soak also remain open.
