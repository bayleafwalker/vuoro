# Mandatory catalog revision: proposed transport profile for agentops#2604

Status: proposed owner contract and consumer inventory only. No runtime protocol,
client default, deployment or catalog is changed here. Current protocol 1 remains
legacy compatible and unqualified for the frozen exact-revision invariant.
Governing outcome: resource contract Version 3 in
`docs/plans/2026-10-03-resource-authority-contract.md` and agentops#2604.
Public source measured at `9c02d8f9b7cc42f9b90c4a302b623b66d54b4266`.

## Measured behavior and inventory

`InvocationRequest` defaults to `invocation/v1` and makes `catalog_revision`
optional. `_dispatch` refuses a supplied stale revision before identity resolution,
but a missing/null/empty revision bypasses that comparison. The published-owner
resource gap receipt demonstrates an omitted revision dispatching a write.
Service source is version 0.1.84, client 0.1.1 and edge 0.1.6 at this commit;
these are source identities, not a claim about every installed consumer.

| Current public consumer | Revision and retry behavior | Migration requirement |
| --- | --- | --- |
| `AsyncVuoroClient.invoke` | Discovers/sends revision; stale response clears catalog cache and raises; does not resend the invocation | Explicit strict profile selection, protocol/schema/routes v2; retain fail-to-caller behavior |
| `ShellWorkSource` list/describe | Sends cached revision; automatically refreshes and retries once on stale-catalog | Remove transparent invocation retry for strict profile, including reads; forward stale result to caller |
| `RecordShellClient` | Sends null revision; no stale retry; shared caller-forwarding path for run/evidence/claim/outcome records | Bind the discovered strict catalog revision on every admitted call; preserve caller credentials and no local owner authority |
| `ShellIntentStore` | Sends null revision for proposal/get/run-resolve; public catalog availability check is not a bound revision | Strict catalog binding for each call, including multi-call reads; no automatic fallback or invocation retry |
| `SprintctlIntentSource` | Caller supplies authenticated invoke transport; class itself cannot establish its protocol | Inventory and pin the supplied transport and profile; class name alone is not proof |
| `scripts/settlement_scenario.py` ShellCaller | One actor sends revision, another deliberately omits it for legacy scenario coverage | Preserve legacy fixture; add explicit strict-profile scenario rather than silently rewriting history |
| Served validator/direct HTTP fixtures | Explicit stale and malformed legacy-envelope cases | Retain v1 compatibility tests and add separate v2 guard-order oracles |
| Generic resource observation client | Snapshots/changes use schema-driven invoke paths | Strict revision on snapshot, changes and long-poll invocation; refresh catalog never implicitly resumes a refused poll |

Inventory anchors: client `client.py`/`profile.py`; edge `work_source.py`,
`record_tools.py`, `effect_tools.py`; reconciler `sprintctl_source.py`; scenario
and served-conformance scripts. `ShellWorkSource` automatic retry currently
serves list/describe reads, not a claim that every write path retries.

Sprintctl/kctl/auditctl caller packages, workstation/devbox installed profiles,
Cloud/gateway/controller wrappers, direct operational scripts and third-party
clients need separate owner inventory at their actual immutable releases. This
public source survey is not evidence that their deployment inventory is complete.
Private owner payloads are not included here. No production mandatory-guard claim
is permitted while a relevant consumer or reachable legacy invocation path is
unaccounted for. Historical ActionQ drivers are migration evidence, not a new
runtime consumer to preserve by an alias.

## Proposed explicit strict profile

Name: `catalog-required/v2`. It uses client protocol **2**, explicit
`invocation/v2`, `handshake/v2`, `operation-catalog/v2` and a versioned
`vuoro-client-profile/v2` selecting that profile. Profile selection is immutable
consumer configuration, not an optional boolean on one request. A strict client
never falls back to protocol/profile 1 after handshake failure, HTTP 404 or a typed profile refusal,
missing metadata, stale catalog or a timeout.

Proposed discovery/invocation routes are `/api/meta/v2/handshake`,
`/api/catalog/v2` and `/api/invoke/v2`. The old `/v1` routes, schemas, default
profile parser and omission behavior remain intact in the first dual-profile
artifact. Existing client 0.1.1 profiles are not relabeled strict. Legacy clients
must continue selecting only their old routes; strict clients use protocol header
2 and the explicit v2 schema. A protocol/schema/profile disagreement fails closed,
never dispatches through another route and never silently downgrades.

The strict handshake advertises exactly this selected profile and its wire
schema. There is one public, unauthenticated strict catalog per immutable service
composition, independent of principal, tenant, environment and repository. Its
operation visibility is not permission to invoke. Adapter incompatibility fails
startup/readiness rather than producing an identity-dependent catalog. Consumers
select a known profile explicitly; future profiles require their own explicit
configuration, not automatic negotiation or fallback.

The strict catalog revision is SHA-256 of the UTF-8 domain separator
`vuoro-operation-catalog/v2` followed by one zero byte and RFC 8785 JSON Canonicalization
Scheme bytes of the complete profile descriptor and sorted active operation and
resource definitions, including descriptions and validation schemas. The revision
field, ETag and serving-instance metadata are excluded from that input. Adapter
load order cannot change the sorted composition. Changing any included contract
or description changes the revision; deployments cannot alter that composition
through annotations. The artifact pins compatible adapter releases and freezes
the legacy derivation separately. Independent processes loading identical pins
must produce identical strict revision bytes. No input revision trimming, case
folding or other normalization is allowed.

Catalog equality is artifact equality, not a deployment generation or a global
revocation mechanism. A rollback can restore the same deterministic revision.
A request refused as stale before authentication has not consumed its proof or
created a decision; if sent again while still fresh to a matching composition,
its first authenticated admission is not proof replay. Clients must nevertheless
surface the initial refusal and never resend transparently. Another replica's
stale receipt does not establish global cancellation or tell the caller that a
timed-out write was uncommitted. Discovery/invocation replica skew yields the
normal stale refusal; callers explicitly rediscover rather than requiring static
catalog clients to pin a process instance.

Measured authentication policy is separate: `edge_proof.py` uses a 10-second
proof lifetime, 1-second clock-skew allowance, a pod-local HMAC key and one-use
in-memory nonce consumption. `composition.py` constructs the verifier with
`not_before=started_at`, so a proof minted before a verifier restart is rejected
even if that pod retains its key; a new pod has a new key. Gateway assertion
verification also uses a process-start replay watermark. These checks occur after
the catalog guard and must retain their existing replay protection on v2.
The guard does not consume proofs to turn a catalog refusal into revocation.

A stronger public serving-incarnation binding could be proposed separately as
authentication policy, with its own routing/availability tradeoffs and review.
It is neither a frozen catalog invariant nor required for this minimal profile.
No extra affinity or dynamic instance field is added to the catalog hash or
mandatory invocation schema by this proposal.

Strict invocation requires an explicit `catalog_revision` string matching the
current strict catalog's lowercase 64-hex revision exactly. Discovery and liveness
probes are outside operation invocation and must bootstrap without that revision;
this exception grants no operation dispatch. Every operation invocation, including
reads, snapshot/changes polls and writes, carries the discovered strict revision.
Request correlation IDs and catalog ETags are not substitutes for it.

## Dispatch ordering and refusal contract

Order in the proposed strict shell:

1. Enforce bounded transport/body parsing and explicit protocol/schema/profile.
2. Require catalog revision presence and valid type/format, then compare it with
   the immutable current strict catalog revision.
3. Resolve the named operation from that same catalog.
4. Resolve identity, verify gateway/edge proof, bind environment/repository and
   check operation authority.
5. Validate domain arguments, enter owner idempotency/decision admission and
   invoke the handler under the owner's existing semantics.

Missing/null/empty revision returns HTTP 400 `catalog-revision-required`;
wrong type/format returns HTTP 400 `catalog-revision-invalid`; a well-formed
noncurrent revision returns HTTP 409 `stale-catalog`. Protocol/schema/profile
mismatch returns HTTP 400 `client-protocol-incompatible` before dispatch.
A current revision with an unknown operation
returns HTTP 404 `unknown-operation` before identity resolution.
Malformed outer JSON/envelope has its own non-dispatching parsing refusal.
All are typed transport results at the shell boundary; none writes a domain command decision, begins
an idempotency binding or consumes a one-use identity assertion/edge proof.
Observational transport metrics may record the refusal; they confer no authority.
A gateway or wrapper may authenticate before forwarding; this shell ordering is
not an end-to-end zero-authentication claim. The inventory must identify such
boundaries and ensure they do not retry refused invocations. Ordinary bearer or
session authentication may precede shell forwarding; no intermediary may consume
a one-use proof for that strict invocation before an equivalent exact catalog
guard. A separately authenticated outer MCP request is a different operation,
not proof that its inner strict invocation was admitted. Inventory records each
wrapper's proof consumer, guard position and outer/inner request distinction;
end-to-end qualification requires actual wrapper receipts rather than assuming
shell ordering covers the entire path.
Public response metadata may report the current public strict revision, not any
caller/resource identity or private domain state. That revision is informational;
clients cannot rebuild and resend from the refusal automatically.

The identity/edge-proof resolution boundary must cover the new route as well as the old
route, and bind the explicit v2 body/profile, method and path. A protocol-1 proof/body must not be
accepted on the strict route; a v2-bound body/proof must likewise not be
accepted through the v1 route. The guard's before-auth ordering does not authorize
skipping proof validation on valid-revision calls. Body parsing must not perform
adapter validation or side-effecting credential resolution before the guard.
Once a valid catalog request reaches proof verification, its one-use
proof may be consumed even if later domain validation refuses it; that consumption
is not rolled back. A subsequent explicit attempt needs a fresh proof.

A strict client surfaces a stale-catalog result with the original operation
undispatched. It may invalidate its discovery cache but does not resend, re-sign,
refresh an assertion, or choose a legacy route automatically. A later caller-
initiated attempt explicitly rediscovering/revalidating the operation, arguments
and repository authority against the new catalog is a new
invocation, with fresh caller-bound request identity/proof as required. Owner
idempotency semantics remain unchanged; a catalog refusal itself creates no owner
binding. Neither the client nor transport infers that a timed-out write was
uncommitted or silently retries it. This does not prohibit an owner-specific,
explicitly chosen recovery/replay workflow. A partial multi-call flow reports
which component invocations were sent and which refused, so callers recover via
owner semantics rather than assuming preceding components were undone. Agent/MCP
tool loops, wrappers, HTTP libraries and proxies are part of the no-retry
inventory; automatic re-invocation on a stale result is prohibited even when
implemented outside the SDK. A caller-owned subsequent attempt deliberately
reevaluates the operation and authority, rather than mechanically repeating it.

## Alignment with the proposed resource admission owner

The mandatory profile guard is pre-admission transport validation, before the
resource refusal recorder proposed in Sprintctl PR118 (agentops#2603). Missing,
invalid or stale catalog receipts never become resource command decisions and
never charge a resource decision quota. Only after the exact profile/catalog
checks, authentication and repository membership may an otherwise admitted
resource mutation's missing capability enter that owner's private denial ledger.
Operations whose owner requires the strict admission contract, including the
proposed general resource mutations, must appear only in the strict catalog and
have no legacy route alias. Owner metadata and immutable composition enforce that
restriction; a legacy lookup must not reach the strict handler even with a valid
resource grant or a wildcard mapping. During dual-profile serving, already
released legacy operations may remain intentionally unqualified on v1.
The actual profile/schema/routes, framing and internal recorder must be reviewed
jointly before either owner implements a new handler or ledger schema. This
proposal cannot advertise the legacy shell as providing that recorder.

## Required guard and consumer matrix

Use real HTTP shell fixtures with counters/spies for identity resolver, proof
consumption, argument validator, ledger begin/decision write and handler. Do not
prove ordering only by checking the HTTP status.

| Scenario | Required meaningful proof |
| --- | --- |
| Missing/null/empty on read and write | Exact required code; every identity/proof/argument/ledger/handler counter stays zero |
| Wrong type/format | Exact invalid code and the same zero side effects; no string coercion |
| Well-formed stale revision | Exact stale code before auth even with bad credentials, absent operation or invalid domain arguments; no ledger or proof consumption |
| Current revision | Normal identity/proof/capability checks and domain validation execute; unauthorized caller never dispatches |
| Multiple simultaneous faults | Deterministic precedence: malformed envelope, protocol/schema/profile, missing revision, invalid revision, stale revision, unknown operation; later checks remain untouched |
| Mixed profile/schema/protocol | Every mismatch refused; v2 client has no legacy route fallback; v1 envelope cannot masquerade as strict |
| Strict-only operation on legacy route | Absent from v1 catalog and lookup even with resource/wildcard grants; same response as an unknown v1 operation; identity/proof/ledger/handler counters stay zero |
| Catalog hash determinism | Independent processes and reversed adapter load order yield identical hash; included contract/description change alters hash; no revision normalization |
| Restart/rollback and replica skew | Same artifact gives same revision; verifier restart rejects pre-start proofs; stale refusal leaves nonce absent, then a still-fresh matching admission accepts once and refuses reuse; replica stale refusal causes no client retry or global cancellation claim |
| Unknown operation with current revision | Public unknown-operation result before auth/ledger/handler, with no proof consumption |
| Catalog changed between discovery and call | Stale receipt without invocation retry, despite reads or idempotent annotation; explicit subsequent caller invocation uses new discovery |
| SDK/edge/reconciler transports | Mock/real HTTP call count stays one per attempted invocation on stale/error/timeout; no automatic re-sign or redispatch |
| Multi-call effect/get path | Every component has its own exact bound strict revision; a stale component stops the flow, not a hidden refresh/continue |
| Resource long poll | Stale before owner/poll registration; no implicit reconnect after catalog refusal; explicit cursor recovery belongs to owner contract |
| Legacy compatibility | Pinned golden status/code/body fixtures for protocol1 omission/null and explicit stale refusal, plus SDK1 profiles, retain their recorded behavior during dual-profile stage |
| Profile artifact identity | Immutable profile/schema/catalog digest bound across handshake/catalog; ETag corresponds to that deterministic catalog; refresh cannot select a different profile |
| Intermediary proof boundary | Actual gateway/wrapper fixture demonstrates that a stale inner strict invocation leaves its one-use proof replay store untouched; separately measured outer MCP authentication is not conflated with inner admission |
| Proxy/library retry settings | Stale, 502/503 and timeout cannot trigger automatic invocation replay through connector, agent loop, HTTP transport or proxy configuration |
| Proof boundary | Valid strict calls verify body/profile/correlation and replay protection; rejected catalog guards consume nothing; no unguarded new-route bypass |
| Strict-only retirement | Every legacy invocation route returns the documented incompatibility without auth/ledger/handler; no reachable alias permits omission |

After source implementation, public tests bind released immutable client/service
artifacts and actual pinned adapters. Existing omission counterexamples remain
clearly classified legacy gaps until their reachable profile is retired; do not
turn those tests into green strict-invariant passes by changing their labels.

## Publication and owner decision gates

This document proposes the version/profile choice; it does not enact or ratify a
breaking runtime change. Joint owner review must resolve the precise schemas,
profile parser, proof binding and consumer inventory before implementation.
The new HTTP 400 choice is deliberate for the proposed v2 mismatch/retirement
contract; existing v1 version-header mismatch behavior remains unchanged during
dual-profile serving. This proposal does not misuse HTTP 426 without a transport
Upgrade contract.

First implement/review both profiles in an immutable dual-profile artifact,
retaining legacy behavior, including only v1 ShellWorkSource one-shot read retry. Publish exact service/client/edge and supplied
reconciler transport artifacts; validate profile selection and conformance at
those immutable pins. Migrate consumers explicitly, recording each profile,
caller forwarding behavior, inventory completeness and no-retry proof. Mere
source defaults or catalog availability are not migration receipts.

Only after the inventory and strict-client receipts are complete may a separately
reviewed immutable strict-only artifact retire legacy invocation serving. The
proposed retirement result is HTTP 400 `client-protocol-incompatible` on old
invocation routes before identity/ledger/handler; no automatic downgrade or alias.
Deploying that artifact requires its own root/owner rollout authorization and
receipts. Do not toggle runtime guard/catalog semantics with a deployment overlay.

The dual-profile stage qualifies only its strict profile's demonstrated outcomes,
not the entire reachable provider. Production-wide mandatory-guard claims require
strict-only serving, all consumer/profile pins, actual rollout/rediscovery proof
and no reachable compatibility bypass. Rollout receipts state the expected
replica-skew refusal window and explicit caller rediscovery path without affinity. Full resource qualification still depends
on agentops#2603 and its other owner gates. This lane does not share the G2
production deployment window or implement Cloud mapping/deployment #2600.
