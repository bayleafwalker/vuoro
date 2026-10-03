# Resource authority contract v1: harvested outcomes and carrier qualification

Status: normative outcome contract and conformance harvest for agentops#2595.
Sprintctl 0.12.0 is **not qualified** for resource contract v1. Supported carrier
observations and explicit gap receipts are recorded. The counterexamples are
tracked by agentops#2603 (ownership, cycles and resource decision carrier) and
agentops#2604 (missing catalog revision).
This page confers no new hosted merge, signing, execution or effect-apply authority.

Source: ActionQ's accepted tranche-4 freeze,
`docs/plans/2026-08-20-tranche4-federation-storage-contract-freeze.md`,
“Chosen federation contract v1” and all nineteen “Frozen invariants”, plus
`test_federation_revision_authority.py` and
`test_federation_ownership_authority.py`. This is the external contract home;
ActionQ remains historical evidence, not a runtime dependency. Direction §7.1
separates the required outcomes from migration compatibility. Leases are covered
by the separate lease suite and do not belong to this resource contract.

## Outcome contract

An opaque resource identity and its immutable creator principal identify the
aggregate. Principal identity is minted once as `issuer:subject:epoch` (Vuoro
#53); a reused display actor, fresh grant, assignee or current lease never
transfers ownership. Creation requires expected absence/revision zero and emits
revision one. Every accepted mutation compares the exact prior revision, advances
it once and appends exactly one authoritative change atomically. A refusal changes
neither the resource nor its history but leaves a durable command decision.

States are registered, evidence-recorded, accepted, rejected and superseded.
Recording evidence is allowed from registered or evidence-recorded; acceptance
requires evidence-recorded and binds identity, reviewed revision, content digest
and accepting principal. Rejection is allowed from registered or evidence-recorded.
Recording settlement appends a local fact to accepted/rejected without changing
that state. Supersession is terminal and allowed from any other state. These are
local facts: acceptance and settlement do not execute native work, settle a work
item or confer an external authority's decision.

Relations are source-owned directed parent-of, depends-on, derived-from and
supersedes edges. They mutate only the source revision. Self relations are refused;
All relation edges point source → target, including parent-of and depends-on;
parent/dependency cycles, including mixed and concurrent reverse edges, must be
refused atomically. Targets keep their identity, revision and owner. Relations,
external references and supersession do not transfer ownership. External
attestations retain typed assurance and provenance and never become leases.

Evidence is content addressed. Verify available bytes against the named digest
before recording/accepting them; missing or mismatched content cannot advance
acceptance or native execution. An immutable WorkRelease binds material work
constraints; changing them creates a successor release, preserving the old release
and supersession linkage. Mutable item descriptions are not that release.

The independent command-decision ledger binds environment, immutable principal,
operation and idempotency key to canonical request digest. Same-key/same-digest
replay returns the original response bytes after lost response or restart. A
conflicting digest gets its own durable refusal and never replaces the first
binding. Accepted and rejected decisions preserve status, code/message, response
bytes/digest, resource and before/after revision. Canonical JSON is sorted, compact,
Unicode preserving and rejects floats/NaN. CAS locks and successful-result replay
alone do not supply this rejected-command ledger.

The authoritative projection equals replay of contiguous changes 1..N. Gaps,
duplicate positions and digest conflicts fail closed. Backfill provenance remains
distinguishable from native facts. No executor is inferred from a change stream;
checkpoint/tail retention requires its own ratification.

## Actor and database authority matrix

| Role | Permitted outcome | Ownership/state constraints |
| --- | --- | --- |
| Creator | Create absent resource | Authenticated immutable creator, revision 0 |
| Relation writer | Add source relations/references | Own source, exact revision, valid target, no cycles |
| Evidence ingester | Record verified evidence | Registered/evidence-recorded, exact revision |
| Acceptance reviewer | Accept or reject | Reviewed identity/revision/digest; valid source state |
| Reconciler | Append local settlement fact | Accepted/rejected; no native execution authority |
| Owning superseder | Supersede | Own source, exact revision; terminal |
| Reader | Read current resource/history | Authorized scope; no mutations |
| Archive reader | Read retained historical export | Archive scope, no mutations |
| Backfill principal | Import provenance-marked facts | Ratified import only, no acceptance/execution grant |
| Migration owner | DDL and migration ledger | Not an end-user/domain mutation grant |

Service database roles receive least resource/change/decision DML and no DDL;
end actors never receive database access. Legacy execution roles receive no new
resource-domain rights. Application roles, service database roles and migration
ownership are separate authority planes. Authentication and an actor name alone
are insufficient grants.

## Frozen invariant crosswalk

Classification codes expand to the five direction §7.1 classes: **S** essential
safety/recovery; **W** essential workflow; **M** migration-only;
**C** incumbent convenience; **U** unresolved. A missing carrier does not change
an essential invariant into convenience. Compound historical paragraphs retain
their historical number and explicitly distinguish permanent outcomes from the
legacy migration obligation.

| Freeze invariant | Class and required outcome | Carrier / qualification |
| --- | --- | --- |
| Historical 1 | M: preserve migrations 001–012, checksums, rows, order, receipts, decisions, publications, changes and completion cursor under retention | Legacy ActionQ archive/export and verified import; not Sprintctl schema numbers |
| Historical 2 | S: independent authority domain; M: no legacy migration 013/queue-claim FK | Domain-owner boundary; Sprintctl work owner and protected effect owner; no lease coupling |
| Historical 3 | S: stable identity/digest and no duplicate recovery; M: monotonic restartable legacy backfill | Protected import owner/S4; generic resource replay carrier remains open in #2603 |
| Historical 4 | S: least DML/no DDL for service, no end-actor DB access, no accidental legacy grant | Deployment role/identity owners; HTTP reader denial is covered; DB-role migration proof belongs to import/deployment owner |
| Historical 5 | M: explicit migration selection, execution v12 not federation readiness, byte-identical historical assets | Archive/import tooling; not a challenger readiness test |
| Historical 6 | S: ratified retention/export/restore before destructive work; M: legacy backfill gate | Evidence-home/import and operator owner; no destructive authority here |
| CAS 1 | S: absence/0 creation, exact N → N+1, one change, refusal leaves projection/history unchanged and durable decision | `work.item.edit` carries description CAS only; full resource/rejected-decision gap #2603 |
| CAS 2 | S: independent idempotent command-decision ledger; locks not authority | Owner idempotency ledger covers successful results; resource rejected-decision carrier gap #2603 |
| CAS 3 | S: receipts not revisions; typed external assurance not leases | WorkRelease/effect evidence and protected acceptance owner; resource assurance carrier open #2603 |
| CAS 4 | S: no prune, projection == contiguous replay, detect gaps/duplicates/digest conflicts, no executor; U: checkpoint/tail ratification | Owner resource change contract needed #2603; maintenance resource observation is a distinct type |
| CAS 5 | S: deterministic rebuild and explicit native/import provenance; M: historical backfill mapping | Protected import owner/S4; generic resource carrier gap #2603 |
| CAS 6 | S: verified CAS-addressed evidence bytes before acceptance; missing/mismatch cannot advance/execute | Protected digest-bound acceptance owner; `work.effect.accept-v1` and reconciler hash binding are partial carriers, not evidence-home byte availability |
| Version 1 | S: explicit independent schema/package/catalog versions; C: old catalog shape/names frozen | Vuoro handshake/catalog and immutable owner pin; legacy shape belongs in compatibility layer |
| Version 2 | S: authority contracts exclude execution/claim/settle/harness policy, removed names unsupported; C: exact generic catalog names | Domain-scoped operation catalogs and protected owner grants; no ActionQ compatibility alias required |
| Version 3 | S: composed catalog SHA and exact revision guard before handler/ledger, stale/missing refused, no transparent retry | Vuoro shell rejects stale supplied revision; missing-revision legacy allowance is a documented transport compatibility gap #2604, not full qualification |
| Version 4 | W: merge → immutable artifact → owner schema readiness → exact consumer pin → release/deploy → rediscovery/audit/fence | Release/composition owners; published Sprintctl 0.12 and runtime 0.1.83 receipts; Cloud mapping/rollout #2600 still pending |
| Version 5 | S: compatibility facades cannot import application/execution storage policy; C: historical root CLI/import paths | Package boundary tests; legacy facade compatibility is not challenger outcome authority |
| Version 6 | S: pre-cutover rollback retains facts, disables new writes, bridge audited; M: old schema retention | Release/import owner; no live cutover performed by this harvest |
| Version 7 | S: post-fence rollback never restores legacy claims; deletion requires retention/operator approval; M: legacy archive sequence | Release/archive owner and operator; separate from package test authority |

## Essential scenario placement and owner mapping

`packages/vuoro-service/tests/conformance/resource_contract.py` is a test reference,
not a new runtime owner. Its neutral scenarios exercise aggregate identity/CAS,
source-only relations and combined cycle refusal, epoch ownership, each mutation
role denial, evidence/digest acceptance, acceptance versus local settlement,
supersession, first-binding/rejected replay and Unicode canonicalization.
The ownership projection is a derived query over immutable creator fields; it
has no writable side table. It deliberately cannot infer creator ownership from
Sprintctl work-item assignees, reservations, leases or accepting actors.

`resource_owner_scenarios.py` is invoked explicitly by the configured PostgreSQL
CI job with the immutable published owner. It uses the real authenticated Vuoro
HTTP shell, not a direct invocation that bypasses central authorization. Supported
observations: description CAS preserves aggregate identity, stale writes leave the
projection unchanged, a reader cannot edit, and a stale catalog cannot dispatch.
Sprint and track fixtures are initialized directly on the disposable owner store;
resource creation and all observed mutations use authenticated HTTP. Its explicit **gap receipts** execute real counterexamples: opposite dependencies both
commit; an epoch-one principal with repo write authority edits an existing item, and an
omitted catalog revision dispatches a legacy-compatible write.
Passing those receipt tests means the gaps remain accurately recorded; it does
**not** mean the neutral invariant passed. No xfail/skip turns a failed invariant
into qualification. A changed owner must update the report and linked scenario.

`work.read.release` carries immutable work constraints and their supersession;
`work.effect.propose/get/accept/reject/mark-applied-v1` carry immutable intent,
revision/digest CAS and acceptance/application separated from work settlement.
The shared ledger/effect suite exercises these against the published owner; the
lease suite covers claim identity separately. `work.maintenance.resource.*` is an
observable maintenance-capability projection, not this general resource mutation
contract. Reusing its transport shape does not supply creator ownership, relations
or an independent rejected-command ledger.

Protected-horizon ownership is mint-once principal identity plus digest-bound
acceptance. The protected owner must prove content availability and append-only
recovery before it qualifies; a hosted capture or runtime read does not add
signing/merge/application authority. Evidence-home S4 and `effect_uncertain`
recovery remain their owners' gates, outside this implementation.

## Non-suite inventory and unresolved qualification

Migration-only scenarios: exact legacy migration numbers/checksums; old queue
ledger/FK absence; legacy backfill selection/restart sequencing; execution-v12
readiness distinctions; archive export/import ordering and pre/post-fence rollback
scripts. Preserve their receipts in migration evidence; do not ask a challenger to
impersonate ActionQ storage or CLI.

Incumbent convenience scenarios: exact old descriptor count/catalog naming,
root CLI/import facade spelling, legacy status/error text, old package layout and
ActionQ migration file paths. Outcome equivalence does not require these APIs.

Unresolved scenarios: checkpoint/tail retention ratification, the chosen general
resource carrier and protected evidence/import authority readiness. General
aggregate ownership/relations/decision/rebuild gaps are #2603; production OAuth
mapping and availability remain #2600. Missing catalog revision compatibility (#2604)
requires an explicit owner/version decision before asserting the exact guard.
Neither unresolved carriers nor accurately reproduced gaps qualify Sprintctl for
the full neutral contract. This harvest is complete only as an outcome/receipt
home; remediation and deployment remain separately tracked.

The reference is intentionally a test model of selected essential outcomes, not a
provider qualification by itself: concurrent relation races, durable process
restart of rejected decisions, database role separation, evidence byte-store
availability and production rollback/import evidence require the listed owner
gates. Those proofs remain open, even when this reference suite is green.

### Qualification inventory

| Executed group | Meaning of green | Remaining carrier gate |
| --- | --- | --- |
| 18 neutral reference scenarios | Specified outcome oracles work in the test model | Not owner support; generic resource owner #2603 |
| 2 supported owner scenarios | Description CAS/identity, stale-write refusal, reader denial, stale supplied catalog fencing are observed through HTTP/PG | Description-only CAS does not qualify the aggregate |
| 3 owner counterexample scenarios | Current epoch-owner, cycle and missing-revision gaps are reproduced accurately | #2603 and #2604; not invariant passes |
| 1 unsupported owner-operation scenario | Proposed resource creation/relation/decision carrier names are explicitly refused | #2603; names are candidates, not mandatory neutral API spelling |
| Existing ledger/effect/lease suites | Their stated published-owner contracts pass | No inferred generic resource or deployment authority |

The unsupported-operation receipt is interpreted together with the owner catalog
mapping: maintenance resources have observation/recovery semantics, work items
have repository-team semantics, and effects have intent semantics. Refusal of a
candidate name alone would not establish an outcome gap in a differently named
provider. No local adapter implements missing mutations to manufacture a pass.
