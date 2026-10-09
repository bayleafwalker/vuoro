# Evidence evaluator composition qualification

Candidate service **0.1.90** consumes the actual published Sprintctl **0.16.0**
wheel, SHA256 `d74dff63da4bc9d75561d2d7ea731b7a509948abc9b485fcc2d1f9ca93a68d1a`,
from source `e7fc9e01e31d0637f4d160920e04b98fd2bf4d80`. Its verified GitHub
attestation binds the wheel to `v0.16.0` and `release-sprintctl.yaml`.
[Sprintctl PR137](https://github.com/bayleafwalker/sprintctl/pull/137) adds the
read-time snapshot/validity prerequisite. Audit0.1.9, adapter-kit0.2.0,
schema-runtime0.1.0 and work schema21 stay pinned to their previous artifacts.

Three gates passed in a new isolated environment containing the actual owner
and shared releases plus the built candidate service. Installed dependency
consistency passed. The resource-disabled work catalog has **74** operations and
metadata SHA256 `8326e462caaabca366bf975ca182ef149603fabab17bb1d09c8d289c8de38579`;
the composed work/audit catalog has **79** operations and revision
`29c2dd503c4de2347fd1ba481b23a80b5465e791ce64a5ba8e8b37c2f8185090`.
Comparing the actual installed old0.15.1 and new0.16.0 owner catalogs proves all
73 previous descriptors byte-identical and only `work.evidence.evaluate-v1`
added. A forced incorrect expected fingerprint made the composition gate fail.

**17 new HTTP histories** pass; existing bound proposal cases pass too.
The complete installed conformance suite passes **69** with no skips. Mandatory
version and site-packages origin checks prevent editable workspace imports from
satisfying this proof. CI runs this isolated composition before its historical
owner downgrade. The HTTP fixture is the real production shell's in-process
TestClient with static synthetic identities and a disposable PostgreSQL16 owner.
It uses a resource-enabled work-only catalog; the separate released-composition
gate uses resource-disabled work+audit. The evaluator descriptor is identical
between those catalogs. This is not a deployed OAuth/enrollment proof.

A reader holding only `work:read` plus `work:evidence` succeeds without write or
effect authority; either scope alone refuses. Changed principal, workspace,
client or grant, foreign repository, extra identity/trust/fact fields and an
envelope idempotency key refuse. Empty and populated responses satisfy the
closed owner result schema. Exact-tail conflict, independently edited description
and an old frozen Release beside a newer caller revision stay explicit.
Expired, missing-input and malformed legacy validity remain distinct.
Schema-valid authored success, use and non-invocation assertions remain authored:
execution facts are empty, authority coverage unsupported, effect unknown,
recommendation reconcile, and execution authorization false.

Successful and refused reads preserve complete rows in eight relevant tables:
run, work item, evidence, Release, reservation, intent, Decision and idempotency
ledger. This is a scoped no-write oracle, not an exhaustive database comparison.
The first HTTP run exposed test assumptions about null error results, HTTP422
schema rejection, claim required fields and edit authority; only fixtures and
expectations changed. A combined consumer invocation hit existing package
`conftest` name collisions; separate CI-shaped commands passed.

The source service suite passes **317**, with 36 unconfigured PG skips; full
workspace **1600**, with 43 such skips. Configured current-owner accepted-discovery,
ledger/effect/provider and explicit resource observations pass **1 + 104 + 6**.
Resource gap receipts remain nonqualifying. Astra approved this bounded source.
The local takeover, killed-process restart and stale-lease restart settlement
scenario passes, including disposable sprint cleanup.

## Remaining gates

Service release/wheel and OCI provenance, exact-image pull/qualification,
consumer GitOps repin and runtime convergence remain separate receipts.
This change grants no production identities or capabilities. The complete S4
work still needs authenticated execution-source integration (through an additive
evaluator version preserving v1), real inventory import, production writer
qualification, attribution, cutover and soak. Authored assertions, OAuth binding,
proposal acceptance and lease loss cannot supply the missing execution facts.
