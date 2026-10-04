# Joint resource owner and mandatory catalog source plan

Status: proposed implementation plan for agentops#2603 and #2604. This document
executes no schema, registers no grant, and changes no runtime. It does not
ratify the two proposed contracts, claim resource qualification, or authorize a
production rollout. Root review advances bounded source work under the existing
program; normal independent review and CI remain source landing gates.

## Governing revisions and measured interfaces

Frozen outcomes: `docs/plans/2026-10-03-resource-authority-contract.md` at Vuoro
`9c02d8f9b7cc42f9b90c4a302b623b66d54b4266`, especially creator/graph/ledger
invariants and Version 1–7. Historical names and migrations are not aliases for
new authority. Proposed owner contract: Sprintctl PR118 head
`4ac5dafc387b32ab51c60ae7bdf08267701974ca`. Proposed strict profile: Vuoro PR175
head `4f7dfe5d43998ea793dbb17a23eec0200da9e01a`. Neither is merged or ratified.

Fresh canonical source bases: Vuoro `9c02d8f`, Sprintctl
`f6936f410f41a7dfaf7e5a1390c552eb90954874`. Shared primary checkouts are not work
surfaces. Stateful verification uses temporary SQLite and disposable PostgreSQL
with independent actor connections, never the served maintenance backend.

Required surfaces inspected: Vuoro `client-authority-boundary` and
`service-compatibility-and-migrations`, its boundary overlay; Sprintctl
`claim-ownership`, document-linked-work and state-protocol overlay. This change
adds a separate aggregate, not claim/lease repair. Existing work/team editing,
terminal work decisions, grant-bound run/evidence records and effect intents
keep their owner semantics. Resource authority is not S4 reserve/evidence/propose
intake; the three missing typed intake carriers remain independent work.

| Source interface | Measured behavior | Required source change |
| --- | --- | --- |
| adapter-kit `catalog.operation_spec` 0.1.1 | Pure spec builder, one required authority, no admission profile | New explicit v2 builder/descriptor; existing v1 output byte shape unchanged |
| service `contracts.OperationDefinition` | Strict model rejects unknown fields; no profile/admission metadata | Separate v2 definition; do not silently add fields to every v1 dump/hash |
| service `CatalogRegistry` | One registry; v1 revision hashes JSON definitions; get occurs before identity; input validation before handler | Separate immutable legacy and strict views; strict hash and closed internal recorder registration |
| service `_dispatch` | Optional catalog; capability rejection precedes repo authorization; no owner denial callback | Strict route guard and admission order below; legacy order retained |
| `InvocationContext` | Identity, request ID, basis/catalog/key/repo; no profile or trusted ingress marker | Explicit strict context carries actual profile plus typed shell-established provenance; no wire-selected trust |
| Sprintctl `application_common.InvocationIdentity` | Structural declaration has actor/environment/authorities only | New resource identity protocol requires minted principal and repo scope; no actor fallback |
| Sprintctl `register_work_catalog` | Converts raw specs into service definitions; wrapper invokes WorkApplication | Separate strict resource registration entrypoint, adapter-kit v2 bridge and resource-only recorder hook |
| service composition | Immutable owner locks, schema check, one registry; runtime does not migrate | Validate profile-aware released descriptors, schemas and verifier dependencies before readiness |
| client `Profile` / loader | v1 schema only; no protocol field | Versioned v2 parser and explicit profile; existing v1 parser unchanged |
| SDK / edge / intent transport | SDK does not resend stale; edge read source retries once; record and intent clients send null | Explicit strict transport binds discovery; remove strict retries and bind every component; preserve legacy fixtures |

## 1. Freeze registration and transport metadata first

Choose separate `operation_spec_v2` and `OperationDefinitionV2` for strict
registration. V1 builders/models stay unchanged. The v2 spec declares
`admission_profile = catalog-required/v2`, `legacy_availability` (released-legacy
or strict-only), required authority set, and owner admission contract ID when
present. All resource mutations require their narrow operation capability plus
`work.resource.read`. The strict catalog hashes that metadata. Existing released
operations can be adapted into the strict view with one authority; resource
operations can never enter the legacy view. No default profile on a new strict
owner spec, and no permissive downgrade of unknown metadata.

Adapter-kit remains pure schema/data validation, with no database, authentication
or application code. Define the admission protocol as pure structural types in adapter-kit, with no
service/database back-dependency: immutable principal/context fields, validated
admission request, immutable public response bytes/status and resource-owner
callback protocol. Service constructs actual trusted instances; Sprintctl consumes
the protocol without importing shell internals. Callback objects are never
serialized into the catalog. Registration accepts a typed internal owner-admission object only for
a declared resource mutation contract. It binds one owner and exact registered
operation names. Duplicate names, undeclared callback names, strict-only legacy
placement and missing callback for a claimed durable admission contract fail
composition. No callable recorder is exposed as a client operation.

Build two immutable catalog views from the same pinned composition. V1 retains
its old serialization and hash. V2 uses the PR175 descriptor plus sorted active
operations, resource kinds and observation transport definitions. Its descriptor
includes protocol/schema/profile identifiers, guard contract version, supported
features and admission metadata, not build timestamp, process ID, deployment
annotations or a gratuitous package patch version. Actual included contract
changes alter the hash; package-only patch changes need not. Top-level ASCII
operation/resource names follow current grammars; nested arrays remain ordered
and adapters must emit deterministic order. Reject non-JCS Unicode/numbers,
including lone surrogates, instead of coercing them. Use a tested RFC8785
implementation, published as an exact transitive release lock if it is not
vendored under repository dependency policy. Independent processes and reversed
registration order must produce the same SHA; changed descriptions and admission
metadata must change it. Hash recurrence means artifact equality, not revocation.

Profile views cannot vary by principal, repository, tenant or database readiness.
A strict owner declaring a minimum schema must fail readiness on an old schema,
not quietly omit operations and publish a different catalog. If a neutral
strict-shell release predates the resource adapter, it advertises only its
actual pinned released operations; adding the resource owner is a new immutable
composition and catalog revision. Discovery remains public and grants no dispatch.

## 2. Strict shell and identity boundary

Implement separate v2 handshake/catalog/invoke envelopes and routes, plus the
explicit client-profile/v2 parser. Preserve v1 defaults and schemas. Deterministic
strict refusal precedence is bounded outer framing, protocol/schema/profile,
revision presence, lowercase format, exact match, then operation lookup. Every
read/write/poll uses this guard before identity/proof/domain validation/ledger.
Missing/null/empty, uppercase, wrong type and stale revisions get PR175 typed
codes; counters establish zero proof consumption, ledger writes and handler
calls. Valid unknown operations likewise stop before identity. Strict-only names
are unknown on v1, which currently also resolves operations before identity.

For resource mutations only, after the guard/known operation:

1. Resolve authenticated identity and proof normally; check environment.
2. Require and authorize the envelope repository; require an actual minted
   principal triple. Validate bounded command framing and explicit key, without
   reading a resource. Unauthenticated/foreign-repo/invalid-principal/framing
   requests are pre-admission transport refusals and charge no owner quota.
3. Establish review ingress eligibility where required. A caller-controlled
   header/body reviewer flag cannot satisfy it. Wrong ingress is pre-admission.
4. Pass the typed owner admission path both the normalized command and the
   complete authenticated authority set, even when a required mutation capability
   or its additional read capability is absent. The owner first binding and
   quota transaction decides/replays the durable private refusal. Ordinary
   operations retain normal shell capability checks and never use this path.

The callback must not run a handler under a substituted privileged identity.
Its context is immutable, authenticated and built by the shell; users cannot
serialize it. The owner independently validates capability/principal/repo/profile
conditions before any mutation. The shell formats stored public status/body;
returning an owner response never proves acceptance when owner state refused.
A forged callback payload, foreign operation or absent published owner contract
fails closed. Read denials remain ordinary transport results; no command key is
invented for them.

Resource input validation belongs inside this admitted owner path so semantic
invalidity can produce its permanent decision. Do not call the normal registry
schema validator first. Digest classification depends only on bounded argument
bytes, never the current domain schema: parse using one frozen strict UTF-8 JSON
parser that detects duplicate keys and rejects lone surrogates, nonfinite values,
float tokens (including exponent/-0.0 spelling) and unsupported framing. Values
that parse into the frozen integer/bool/null/string/list/object domain use sorted,
compact, Unicode-preserving JSON canonical command bytes (not the catalog JCS
algorithm). Integer -0 canonicalizes to0; arbitrary bounded integer tokens remain
integers without float conversion. Semantically invalid but canonicalizable
arguments use the same canonical class. Other bounded valid outer requests with
noncanonicalizable argument bytes use domain-separated `raw-json/v1` exact raw
argument bytes; duplicate keys never silently choose first or last. An invalid
outer invocation stays pre-admission, while bounded unsupported argument JSON
can receive a durable private refusal. The envelope parser must preserve raw
argument slices and cannot coerce them before classification. Whitespace variants
collapse only in the canonical class; raw-class whitespace differences intentionally
conflict. Schema changes cannot switch classes for the same bytes; changing these
rules requires a new operation/digest version. Freeze parser vectors in kit/owner
and test both published releases against them.

A single typed entrypoint owns validation, decision and effect; no separate
shell/handler transactions decide the same request. Within admitted first attempts,
owner refusal order is relevant required capabilities, command-domain shape,
resource visibility/ownership, CAS, then state/graph/binding semantics. No resource
read occurs for a missing read capability; fixed private denial discloses no graph
or resource existence. Identity/repo/framing/trusted ingress were checked before
admission and are independently rechecked by the owner. Stored decisions contain
only relevant capability evaluation, never the complete authority set or secrets.

Authenticated identity is existing `Identity.principal_id`, parsed from the right
into exact authenticated issuer, colon-free subject and canonical nonnegative
decimal epoch (no sign/leading zero). No issuer case-folding/URL rewrite is done;
identity issuance must pin its canonical issuer string. Epoch equality is exact. Gateway resolver already verifies
signed `subject` and integer `principal_epoch` and composes the triple; epoch
cannot be replaced with iat, actor or zero. Static identities must supply the
owner-issued triple; `None` and reserved system principals are ineligible for
this resource's human/end-principal ownership/review path. Epoch zero is valid
only when actually asserted by the issuer. Creator remains the exact immutable
triple. Review separation compares issuer/subject excluding epoch, so reissue
never enables self-review. Principal lifecycle mint/reissue remains issuer-owned;
this plan cannot claim that a syntactically valid static string proves lifecycle
safety on a real installation.

## 3. Owner storage and atomic admission

Add an explicitly separate Sprintctl resource store/module, invoked from a new
resource application surface; do not alias work_item, maintenance resource or
effect intent tables. Candidate migration allocation is local schema26 and remote
schema21 after current25/20; remeasure before implementation and never reserve
those numbers through this document. Owner migration assets and readiness checks
are Sprintctl-owned; runtime/startup roles execute no DDL. The authoritative resource application has
no direct CLI/library operation accepting a self-asserted actor or principal. It
requires the shell-established authenticated admission context and store binding.
SQLite is an explicitly non-authoritative disposable parity/migration reference;
its fixtures cannot synchronize/promote self-asserted identities or records into
the served owner. A future direct authoritative transport needs its own verified
identity carrier, not the ability to open a file. Existing owners retain
current table and idempotency contracts.

Proposed logical tables, all repository/environment scoped:

- resource projection (opaque minted ID, immutable creator, revision/state,
  final journal digest); immutable resource changes with unique position and
  predecessor digest, positions exactly1..revision;
- retained typed edges/references and immutable evidence/review/settlement
  binding facts tied to resource journal mutations;
- first command binding keyed by environment/repo/principal/op/key, immutable
  command digest, stored public status/body bytes and decision identity;
- separate immutable conflicting-digest refusal binding, indexed by original
  first binding and conflicting digest; it never overwrites first result;
- explicit bounded aggregate environment/repo admission counters and
  principal/repo counters. Permanent decisions count both. No eviction,
  expiration, destructive maintenance or hidden journal compaction.

One transaction acquires graph advisory lock first for parent/dependency commands
(PG READ COMMITTED; later statements read the graph), then aggregate/principal
quota locks, first binding, source row. SQLite starts BEGIN IMMEDIATE before
admission reads. Replay lookup precedes capacity rejection after applicable
locks, so exhaustion cannot hide committed results. Missing capability, stale
CAS, invalid state/graph/evidence and noncreator failure commit an immutable
private refusal but no projection/change. Capacity exhaustion returns visible
service-unavailable before a new decision/effect; it is an availability failure,
not complete resource support or caller denial. These are lifetime storage admission caps, not rate windows. Aggregate quotas
are configured, bounded and required; no unlimited default. Same-repository
principals without resource grants can collectively fill the bounded refusal
capacity, as explicitly accepted in proposed PR118 and root's review. This is a
visible service availability failure even for capable callers. This plan does
not claim capable callers retain an unlimited reserved partition; introducing
separate partitions would change that reviewed owner policy and needs a new
explicit source-plan decision. Tests must demonstrate this exhaustion honestly,
not relabel a denial as complete provider support. An operator can increase capacity via
separate owner policy, never erase decisions to recover space.

First-binding unique losers roll back their whole attempt, then read the committed
winner on a fresh transaction: equal digest returns exact original public bytes
and status; unequal digest admits/replays its separate conflict decision under
quota. Semantic-effect savepoints must not discard the permanent refusal;
infrastructure failures roll back binding, counters, journal and effect together.
Replays do not reevaluate acceptance or mint a new ID. Choose and freeze the replay wire unit before source: the owner decision stores
the complete original resource invocation public body bytes and HTTP status,
including original request ID/timestamps; identical replay emits them verbatim.
A fresh authentication proof may cover the same caller-selected request ID and
idempotency binding; proof nonce/jti is independent. If the outer transport uses
a new correlation header, it is outside the stored public body and cannot rewrite
it. Client resource replay must tolerate the documented original response ID;
no assumption that a replay result is rebuilt around the latest request ID.
Test that exact HTTP body bytes survive lost response and a new process.
Resource get/changes are repository-visible; private decision reads are limited
to original authenticated principal with read-decisions, and audit uses a distinct
capability. Neither general read nor work:write exposes private decisions.

Existing `_idempotent_write` is not this carrier: it is PG-only run/record logic
that rolls rejected effects back and permits corrected argument retry. Existing
`arbitrate_command` has permanent authority decisions but actor-bound work
lifecycle semantics and outbox identities; it is an implementation pattern, not
authority to merge the new store into work decisions.

## Proposed replay authorization decision, unresolved until joint owner review

Frozen exact-byte replay, mutation read requirements and private decision grants
must be reconciled explicitly. Proposed rule: a same-key resubmission recovering
a committed binding is a nonmutating private decision read, not another mutation.
It requires the same currently authenticated immutable principal, current
environment/repository membership and `work.resource.read-decisions`. Original
mutation/read grants need not still exist when that explicit private receipt grant
exists: the capability authorizes exactly the caller's original public receipt,
not a current resource snapshot or another principal's decision. General resource
read alone does not imply this private receipt access. This rule is proposed for
root's combined owner contract decision, not an already frozen qualification fact.

Binding lookup is internal and returns no bytes before this authorization. If the
binding exists but the private decision grant is absent, return a nonreplayable
transport `decision-access-required` refusal with no response-body/resource
information; do not replace the first binding, append a new capability-denial
decision or charge quota. Regranting decision-read permission restores access to
exact original bytes. An absent binding follows first-attempt mutation admission:
all current required mutation/read capabilities are evaluated there and missing
ones commit the fixed private capability refusal. First-attempt result delivery
is the normal admitted-command response, not a grant to enumerate private
history. Callers needing guaranteed authorized lost-reply recovery must possess
and retain the explicit decision-read capability; issuance cannot be invented by
this plan. Reads/audits still use their separate advertised capabilities.

An old epoch with a revoked credential is refused by the real issuer/gateway
before owner admission. A newly issued epoch has a different binding namespace
and can never read the old epoch's private receipt; actor reuse gives no access.
Revoked repository membership stops before binding disclosure and charges no
quota even if decision-read is present. If a deployed static identity mechanism
cannot enforce credential revocation/current epoch, it is not a qualified carrier
for these histories. Future code must not treat a syntactically valid principal
string as a revocation check.

This policy intentionally makes unconditional replay unavailable after receipt
access revocation while leaving the original immutable decision intact. The
frozen replay language and PR118 must receive an explicit combined owner policy
record before implementation; until then there is no complete qualification or
claim that every resubmission returns original bytes. All replay-first/verifier/
capacity statements elsewhere in this plan mean authorized receipt replay under
this rule. The callback knows whether it is recovering a binding before applying
new mutation capability checks; it cannot overwrite the original result with a
new cap-denied result.

## 4. Protected evidence and reviewer carriers remain explicit gaps

Current evidence append validates hash-chain order and stores caller-supplied
ref/digest/metadata. It does not fetch exact bytes or authenticate a protected
owner's current verification. Current effect acceptance binds immutable intent,
not this reviewed resource projection/evidence. Neither satisfies the proposed
resource evidence/acceptance contract by reinterpretation.

Define a narrow protected-owner verifier interface returning authenticated owner
identity, immutable evidence binding ID/record digest, exact byte SHA256 and
current availability/integrity result for the requested protected reference.
Only configured released owner adapters may implement it; caller URLs, arbitrary
HTTP fetching, cached self-reported receipts and injected owner strings are
ineligible. Fail closed on timeout, changed bytes, missing object or unsupported
owner. Acceptance first uses a read-only binding lookup to return any committed exact
replay immediately, without verifier availability. No graph is read by this probe
and it acquires no quota/source locks. For a new binding, remote verification
holds no owner locks; then the mutation transaction follows the declared lock
order, re-looks up the binding (a concurrent committed winner takes precedence),
checks quota and exact resource CAS, and commits one decision/effect. Verifier
timeout/unreachable owner is availability and writes no decision; authenticated
missing immutable object or byte mismatch is a permanent semantic refusal. An
ambiguous missing-object/error response is availability, never guessed permanent.
Before returning an availability result, perform one read-only binding lookup so
a winner committed during verification still supplies its original replay. No
loop/retry of the remote operation is implicit. Current verification must establish
the protected immutable binding at admission; do not hold graph/quota/source locks during a remote call.
The verification must attest an immutable protected byte identity, so successful
verification cannot be invalidated by replacing bytes under that identity. If the
owner cannot provide immutability/current availability semantics across the CAS
window, acceptance remains unsupported pending its owner contract. Verification
response alone is not proof that a protected merge/sign/apply occurred.

A trusted review/reconciliation ingress must authenticate each actual reviewer or
reconciler end principal directly and carry narrow operation grants, not a shared
service identity plus `acting_reviewer`. A general authenticated hosted session
is not enough. Choose a separate shell-verified trusted audience/issuer policy
bound to that principal and route; public normal bearer assertions cannot claim
it. Do not invent a new signed assertion format in this first source slice.
Static bearer resolvers cannot satisfy trusted review ingress. Existing configured
resolvers can supply authenticated identities, but there is
no measured released narrow end-principal reviewer ingress or byte verifier in
the inspected Sprintctl/Vuoro code. Their protected owners must publish contracts
and immutable adapters before record-evidence/acceptance/rejection/settlement are
registered as supported. Test fakes are failure oracles, not published proof.

The core release may expose create/get/changes/relate/reference/supersede and
private decision reads as explicit partial support after their complete tests;
evidence/review/settlement operations stay absent, not runtime stubs reporting
successful support. Full frozen workflow and general resource qualification stay
blocked. Protected native late-outcome recovery also needs its real append-only
outcome carrier and is not supplied by local settlement facts.

## 5. Consumer and immutable release sequence

| Slice | Exact bounded source scope | Release and dependent pin gate |
| --- | --- | --- |
| A | adapter-kit v2 pure metadata/admission protocol + service dual registry/profile/guard/internal admission bridge; existing real operations only plus disposable fake owner oracle; client v2 | Publish kit/client first, then independently reviewed service bridge release; fake owner never enters production composition. No resource schema/grants required. V1 goldens/strict guard/internal recorder refusal proof; deploy none |
| B | SDK/edge strict mode, every record/intent/multi-call/poll transport binds one discovered revision; no retries | Publish edge/client revisions; owner CLIs and wrappers explicitly pin compatible profiles. Legacy remains legacy; not a production mandatory claim; deploy none |
| C | Sprintctl separate resource store/migrations/application + internal owner admission bridge; partial supported operations only | Published kit and service admission bridge prerequisite (no owner back-dependency), independent SQLite/PG histories and HTTP callback denial receipts; immutable Sprintctl wheel before service resource composition pins it. Migration role separately proves readiness; deploy none |
| D | real protected verifier and end-principal reviewer ingress, exact evidence/review/settlement operations | Requires owner-published carriers and pins; no full workflow claim or support until real disposable integration receipts pass; deploy none |
| E | completed consumer inventory + strict-only immutable composition | Retire v1 only after every relevant consumer migrated; separate owner rollout and measured rediscovery/refusal windows; no deployment in this task |

Current measured pins: kit0.1.1 SHA
`0dac880d790857fbed1085906f0e2ffd151c509ab61d527a351b68f3775ee16f`,
Sprintctl0.12.0 SHA
`e669d47f698b6d9509132e2f3dae17323d7b6c17b3c446a66b8172c026c4c5af`,
client0.1.1 SHA
`b5fb6bad174abd00d67504398690bcfb8c3cc3be891e5465983827e5a1740f6d`.
Canonical service source0.1.84 and edge0.1.6 are not future version reservations
or all-tenant deployment receipts. Tentative next additive versions kit0.1.2,
client0.1.2 and Sprintctl0.13.0 must be rechecked against parallel release work;
service/edge releases get their next free versions at publication. Final contract identifiers may appear in immutable artifacts only after root's
joint owner contract/source review freezes their exact semantics and merged source
implements those semantics. This is the already assigned owner review, not an
additional operator approval or an agent changing a document to ratified. The
proposed docs remain proposed unless their owner changes that status. Any early
experimental artifact uses distinct provisional identifiers never silently treated
as final. Source review/publication cannot assign different semantics to one final
identifier. Every new pin records source SHA, immutable wheel URL and SHA, descriptor API/schema version,
transitive release locks and installed-composition attestation. Current remote
compatibility has a bounded minimum/maximum and maximum20; it is not a promise
that old binaries tolerate21. Before any later migration/rollout, independently
prove an additive compatibility release that permits the new schema without
changing old semantics, or use a separately authorized stop/migrate/roll sequence
with explicit downtime and rollback refusal. Never assume migrate-then-roll is
safe; runtime source work here executes neither sequence. Never pin a locally
built candidate as released owner proof. Resource API is separately versioned;
existing work-api/v1 remains compatible while new strict owner contracts declare
an explicit resource API and schema minimum. Update pyproject/lock/pin/startup
matrix together; a package version alone is not protocol qualification.

Cloud/gateway/controller and installed CLI profiles are consumer-owned work.
Inventory each actual immutable release, v1/v2 route selection, catalog ownership,
assertion issuer/subject/epoch forwarding, proof-consumption boundary, partial-flow
recovery, low-level proxy retry settings and agent-loop behavior. Private source
or credentials are not part of this public document. The public assertion
contract requires signed real subject/epoch; wrappers cannot replace them with
an actor or epoch0. New resource capabilities default absent in every existing
mapping; a separate Cloud owner change must enumerate narrow grants and denial
tests. #2600 effects mapping is a different capability/release cause; it does not
authorize resource grants or this profile rollout. No G2 deployment window is
shared here. External unmeasured consumers remain explicit inventory blockers.

## 6. Disposable failure histories and review gates

Before changing source, freeze invocation/completion histories and authoritative
rows for each test below, bind supported-operation receipts to published pins,
and force each incorrect implementation to fail. These are future acceptance
tests, not claims that the proposed code already exists.

| History | Oracle and falsifying implementation |
| --- | --- |
| v1 omitted/null/stale vs v2 read/write/unknown name | V1 pinned golden bytes unchanged; v2 guard zero auth/proof/argument/ledger/handler on refusal. Guard after resolver or coercion fails |
| Reversed adapter order, Unicode/nested arrays, duplicate metadata | Same exact hash independent process; reject invalid composition; changed contributing contract alters hash. Deployment-built/per-principal catalog fails |
| Edge restart and stale refusal | Inspect actual nonce store; TTL/iat/not-before precision boundary fixtures, new pod key, then reuse once-consumed proof refuses. Shared-key test-controlled fixture distinguishes unconsumed fresh admission; no production shared-key claim |
| Stale/timeout/502 through SDK, edge, proxy, agent loop and dropped poll | Mixed-revision pods surface bounded/visible configured rollout refusal window (not hidden guaranteed success); one invocation attempt, explicit partial-flow receipt, no automatic resend/re-sign/reconnect. Retry outside SDK still fails |
| Missing narrow capability after authenticated same-repo admission | One permanent private refusal; later grant cannot change same-key result; foreign-repo/auth failures charge nothing. Shell early denial or handler-only ledger fails |
| Parser/schema upgrade replay | Same argument bytes preserve digest class and original refusal across relaxed/tightened schema; duplicates/surrogates/floats/raw whitespace vectors are stable. Schema-dependent digest selection fails |
| Revocation and lost-reply replay | Real authenticated same-key replay with decision-read returns exact bytes after mutation/read grant removal; decision-read removal refuses privately without new decision/quota; regrant restores bytes; revoked epoch and membership refuse before disclosure; new epoch cannot access old binding. Unconditional disclosure or replacement-capability-denial fails |
| Create response loss and two concurrent equal/conflicting bindings | One minted resource/effect/change, exact public replay; conflicts preserve first decision; forced ID collision refuses. New timestamp/body on replay fails |
| Counter/effect/decision insert failures and admission exhaustion | Full rollback on infrastructure failure; bounded aggregate/principal counters; original replay available at capacity, other new commands visibly unavailable. Eviction or unlimited default fails |
| Zero-capability lifetime exhaustion | Independent principals can reach the configured aggregate refusal cap; later capable new command is visibly unavailable, original replay remains. Claiming authorized admission is unaffected fails |
| Two independent PG graph actors after blocked advisory lock | READ COMMITTED lock first statement; opposing/mixed edges yield one acceptance; SQLite equivalent. Pre-lock snapshot or per-edge-only locks fail |
| Source supersede races and retained superseded target paths | Source CAS one loser; target unchanged; retained edges still prevent mixed cycle. Delete-edge or target-absence shortcut fails |
| Epoch reissue and creator self-review | Old/new epoch cannot mutate each other's ownership; same issuer/subject across epochs cannot review. Actor equality or epoch0 default fails |
| Journal corruption/truncation and derived ownership | Rebuild catches gap/digest/creator/final-anchor mismatch; ownership derived from immutable creator. Silent old snapshot/side ownership table fails |
| Real protected evidence mismatch/unavailability and changed reviewed revision | Real owner verification and CAS refuse without resource advance; cached/self-reported receipt or fake-only evidence fails; unsupported carriers explicitly remain absent |
| Real noncreator reviewer and late outcome | Trusted ingress authenticates actual principal; shared actor-selected reviewer refuses; native outcome remains its protected owner. Local fact cannot pretend apply/recovery |
| Distribution/startup matrix | Transport client has no DB/owner import; direct self-asserted CLI/library path cannot enter authoritative resource storage or sync SQLite fixtures; runtime role cannot DDL; old schema/pin mismatch fails readiness, no auto migration/dynamic omission; published wheel installation reproduces contracts |

Land each source slice only after targeted tests, required state/boundary checks,
independent Opus public review and exact-head CI. Source merge is not acceptance
of #2603/#2604 complete outcomes. Preserve explicit partial support, protected
carrier gaps, consumer inventory and rollout preconditions in served receipts.
The next review should choose A's exact descriptor/registry/context interface and
C's complete owner admission transaction before authoring source; D cannot be
invented from the observation-only evidence chain.

## Product intention crosswalk

This plan preserves agentops target-state TS-1/TS-2/TS-16: the surface records and
coordinates facts and proposes intent; it assigns no execution, routes no model,
and gains no hosted signing/merge/apply authority. TS-5 terminal work decisions
remain unchanged. TS-6 authoritative hosted evidence and preservation of legacy
authored digests are not reinterpreted as protected byte verification or S4 intake.
The new resource journal records only its own coordination decisions; it is not
a second writer of work-item terminal acceptance. Resource qualification depends
on its own complete frozen outcomes, not the existence of current consumers.

Partial operation outcome mapping: create/relate/reference/supersede preserve
frozen immutable creator/CAS/typed reference/retained mixed-graph invariants;
get/changes preserve authoritative contiguous projection/derived ownership;
private decision reads preserve independent permanent replay authority. Each
still depends on Version1 explicit contract/schema versions, Version2 capability
separation, Version3 strict guard, Version4 immutable pins; Version5 package
boundaries and Version6/7 later rollback/import fences remain distinct owner
requirements. None fabricates evidence-recorded/accepted/settled states. General
resource workflow acceptance remains blocked on D. Front-door abuse rate limiting
is shell/edge-owned and separate from permanent owner admission capacity; no
unauthenticated/foreign-repo denial becomes a resource decision. Graph lock is
explicitly per exact repository, transaction-scoped pg_advisory_xact_lock.
