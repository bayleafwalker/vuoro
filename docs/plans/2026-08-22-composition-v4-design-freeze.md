---
doc_id: vuoro-composition-v4-design-freeze-2026-08-22
status: proposed
supersedes:
  - actionq freeze doc W4 "exactly five descriptors" (2026-08-20-tranche4-federation-storage-contract-freeze.md:501-511)
  - 'actionq PR #40 option 2 "second ActionQ distribution" (superseded, not wrong)'
inputs:
  - packages/vuoro-service/src/vuoro_service/composition.py @ 9dc7efa (the v3 model)
  - packages/vuoro-service/composition/adapter-pins.json @ 9dc7efa (the v3 reference composition)
  - docs/plans/2026-08-20-execution-federation-alignment.md (owner boundary decisions)
---

# Vuoro composition v4: capability-based composition and release

Vuoro defines, pins, validates and releases a **supported composition of independently owned
capabilities**. It does not author, operate, or republish every ingredient, and it does not
add services of its own. This document freezes the v4 ontology; it contains no code.

## 1. Why v3 has to change

v3 made Python packaging topology part of the domain model. The evidence is concrete:

- The role taxonomy is a frozenset in service code — `_REQUIRED_DOMAINS` at `composition.py:39`,
  enforced at `:343`/`:345` — and copied into three scripts (`fetch_pinned_adapters.py`,
  `attest_installed_composition.py`, `verify_pre_migration_startup.py`). Adding a role is a
  four-file code change to a running service, not a manifest change.
- A role is a wheel. `RuntimeAdapterDescriptor` binds a domain to exactly one `adapter` lock;
  `composition.py:332-336` rejects duplicate distributions and `:391-393` requires an adapter
  lock to be an exclusive primary. So one codebase cannot serve two roles, and a role cannot be
  served by anything that is not a wheel with a `register` entrypoint.
- Consequently the ActionQ W4 scope review found that "exactly five descriptors" — the freeze
  doc's own requirement — is unimplementable: federation could neither be a second descriptor
  on the `actionq` lock nor a second `actionq` distribution. The constraint that blocked it was
  never a property of federation; it was a property of how v3 spells roles.

The 1:1 descriptor↔lock rule was nonetheless protecting something real: **repinning one role can
never silently repin another**. v4 keeps that guarantee and relocates it from packaging to the
object that actually carries it — the authority binding (§3.5).

## 2. Scope and non-goals

In scope: the manifest ontology, validator semantics, migration from v3, and the proof plan.
Out of scope: any new Vuoro-owned runtime (no `vuoro-runnerd`, queue, placement, leases or
harness adapters — per the 2026-08-20 alignment), the *operation sets* of any owner contract,
and the rescoping of ActionQ federation/W4, which follows this freeze rather than preceding it.

In scope and binding, to remove an ambiguity an earlier draft left: the contract **ids,
cardinalities, scope kinds, frozen status and owners** in §3.2 are settled by this freeze, and
§7's non-transferable-ownership requirement is a contract-level obligation. Only what each
contract's operations *are* remains the owner's to freeze. An owner cannot cite "out of scope"
to treat §3.2 or §7 as advisory.

Three corrections are binding on everything below:

1. **Planes are an operating taxonomy, not service boundaries.** Service boundaries follow
   canonical state, failure isolation, security and upgrade lifecycle. The validator never
   binds a provider to a plane; it binds providers to versioned capabilities.
2. **Vuoro is a compatibility distribution.** Thin and declarative: a support manifest, a
   reference profile lock, adapters and bootstrap material, a conformance and migration report,
   and external artifacts by reference. A Prometheus patch is not a Vuoro release.
3. **Direct operation stays first-class.** Sprintctl, ActionQ and break-glass paths must work
   with Vuoro absent. Vuoro composes owner capabilities; it is not a dependency of them.
   <!-- claim: v4-direct-recovery-without-vuoro -->

## 3. The v4 ontology

Five record types — capability contract, provider, adapter, authority binding, composition
profile — none inferred from another, plus one thing that is deliberately *not* a record.

### 3.1 Plane — a reading aid in this document, not a manifest record

| Plane | Representative capabilities | Today's providers (non-binding) |
| --- | --- | --- |
| Work and tracking | task definition, dependencies, readiness, acceptance, advisory reservations | sprintctl (`work-api/v1`) |
| Coordination | run requests, reconciliation, session lineage, external-execution references | ActionQ federation (external-execution references, assurance, reconciliation) |
| Execution | product-native runtimes (Claude Code, Codex, OpenCode, Copilot), ACP, worktrees | external, qualified owner-side; no Vuoro-owned *runner* — but see the note below on `execution/v1` |
| Authority and policy | identity, principals, grants, policy decisions, credential/secret leases | federation principals and grants; OPA (decisions); OpenBao (secret leases); cred-broker |
| Evidence and provenance | command capture, manifests, knowledge artifacts, audit ledger, retention | auditctl (`audit/v1`), kctl (`knowledge/v1`), federation retention export (W3) |
| Observability and analytics | telemetry collection, metrics, traces, dashboards, derived analytics | OTel collectors → Prometheus; homelab-analytics |

Evidence is first-class and is **not** folded into observability: telemetry may be lossy,
provenance may not.

A plane carries no cardinality, no authority and no validation rule, and the validator never
references one. A grouping with no semantics does not belong in a frozen, validator-gated
manifest, so **there is no plane record**: the table above is documentation. Reports may group by
it, from a mapping that lives with the report and not with the composition.

`execution/v1` is not the Execution plane. The capability carried forward from v3 is ActionQ's
internal, wheel-backed, frozen coordination contract, and it stays exactly that; the Execution
plane row describes the external product runtimes Vuoro deliberately does not own. The two share
a word and nothing else. (An earlier draft cited a `docs/evidence/…` harness-qualification file
for the plane row; no such path exists in this repository, and the qualification is owner-side
evidence rather than a Vuoro artifact, so the citation is dropped rather than repaired.)

Reconciliation with the 2026-08-20 alignment: "placement" and "leases" appear in the
coordination plane only as *capabilities that may have no provider*. ActionQ's queue, claims,
leases and runner are deletion targets; `actionq-dispatcher` is retired; Restate is an
unstarted pilot and is not a packaged component. Listing a capability is not a commitment to
provide it.

### 3.2 Capability contract — the smallest versioned semantic interface

Identifier `<name>/v<N>`. The existing v3 `api_version` strings are already capability ids and
are carried forward unchanged: `work-api/v1`, `execution/v1`, `knowledge/v1`, `audit/v1`.

Each contract declares:

- `cardinality` — one of `exclusive` (one active authority per scope), `multi` (several
  simultaneous providers, e.g. telemetry exporters), or `projection` (derived, read-only, never
  authoritative);
- `required` — whether a profile must bind it at all. Absence-legality is orthogonal to
  cardinality, not a fourth value of it: a capability can be optional *and* exclusive when
  present, or optional *and* multi, and an earlier draft's four mutually exclusive values could
  express neither. The coordination-plane capabilities that "may have no provider" are
  `required: false` with their own cardinality intact;
- `scope_kind` — what an authority binding is exclusive *over* (`tenant`, `project`,
  `environment`, `global`).
- `frozen` — whether the contract's operation bytes/hashes are pinned for the lifetime of the
  profile. This is per contract and not per owner: `execution/v1` and `federation.principal/v1`
  are frozen, while `federation.grant/v1` and `federation.resource/v1` iterate. That split is the
  reason federation is three contracts, and it has a packaging consequence stated in §3.3.
- `conformance` — how a provider proves it: `operation-hashes` (internal wheels, as today) or
  `probe` (external providers, as `validate_served_conformance.py` already does for served mode).

Cardinality belongs to the capability, never to the provider.
<!-- claim: v4-cardinality-is-per-capability -->

Initial contracts beyond the four carried forward. Federation is **three** contracts, not one: identity is frozen forever, grants iterate, and the resource ledger has a different scope from both — one contract would force a single frozen/iterative status and a single scope onto three things that differ in exactly those properties. Contract ids are settled here; their operation sets are the owners' to freeze:

| Contract | Cardinality | Owner | Note |
| --- | --- | --- | --- |
| `federation.principal/v1` | exclusive, frozen; scope **proposed** `global` | issuer, **not settled** | identity is forever (§7). Scope and owner are proposed: ActionQ has no principal registry — `FederationPrincipal` is a value object over an already-authenticated caller and ownership is a plain text comparison — and ActionQ's schema partitions principals by environment, so `global` is a change being proposed, not a property being recorded |
| `federation.grant/v1` | exclusive; scope **proposed** `project` | ActionQ | ACL; iterates independently of identity. Proposed: grants are today the `authorities` set on the principal, and no project dimension exists in the federation schema |
| `federation.resource/v1` | exclusive, scope `environment` | ActionQ | the append-only ledger (W3). The `environment` scope stands on the resource ledger's own deployment partitioning; an earlier draft justified it with the W3 namespace `(mapping_version, environment, source_id, id)`, which is the one-time *backfill import* identity and not federation resource identity |
| `secret.lease/v1` | exclusive, scope `environment`, `required: false` | OpenBao | mechanics only — not identity, grants or decisions; one vault per deployable environment class |
| `policy.decision/v1` | exclusive, scope `global` | OPA | one policy authority, decisions parameterised by tenant/project |
| `metrics.storage/v1` | exclusive, scope `environment` | Prometheus | storage and query only |
| `telemetry.export/v1` | multi | OTel collectors | |
| `analytics.derived/v1` | projection | homelab-analytics | never authoritative |

### 3.3 Provider — an implementation, internal or external

A provider is identified by source and artifact, not by Python distribution. Lock kinds
generalise v3's `{adapter, owner-dependency, shared-dependency}`:

| `artifact_kind` | Identity | Freeze semantics |
| --- | --- | --- |
| `wheel` | distribution + version + sha256 (v3 `ReleaseLock`, unchanged) | frozen |
| `image` | registry reference by digest | frozen **only together with** its deployment closure (§3.6) |
| `chart` | chart + version + digest **+ values digest** | mutable values are not a freeze |
| `binary` | url + sha256 | frozen |

Role in the composition (`adapter`, `owner-dependency`, `shared-dependency`, `external`) is a
separate field from artifact kind, as v3's `lock_kind` already separates identity from role.

A provider may implement several capabilities. **That does not abolish release coupling**: if
two authoritative capabilities must evolve independently — frozen `execution/v1` and iterative
federation — they are separate release units even when maintained in one repository. A release
unit is the provider record; "one repository, two providers" is the normal case, not a special one.
Concretely, and stated because an earlier draft left it implicit: ActionQ ships **at least two**
provider records, one for frozen `execution/v1` and `federation.principal/v1` and one for the
iterative federation contracts, because §4 rule 7 rejects a single record spanning both.
<!-- claim: v4-independent-release-units -->

**Uniform construction protocol.** Every wheel provider is constructed the same way: the adapter
exposes `build(runtime: RuntimeConfiguration) -> Application` and `register(registry, application)`,
where `RuntimeConfiguration` is a declared record carrying the DSN, schema name and whatever else
the profile pins. No owner name, module path, constructor argument or registration convention may
appear in Vuoro service code.

This is the freeze's most load-bearing addition and it is what an earlier draft missed. Removing
the four-domain constant makes a fifth contract *declarable*; it does not make one *composable*.
Today `create_composed_app` hand-wires every domain — `manifest.pin("work")` with a direct
`from sprintctl import …` at `composition.py:885-890`, a hardcoded audit exception at `:1100-1105`
that string-matches `auditctl.vuoro_adapter` / `VuoroAuditAdapter.register` and bypasses
`_load_function`, and a literal `domains = {...}` at `:1109`. Without a uniform protocol a v4
profile could declare `federation/v1`, validate green, and register no operations at all.
<!-- claim: v4-uniform-construction -->

### 3.4 Adapter — a thin Vuoro translation layer that owns no canonical state

The v3 descriptor's `adapter_module` + `register` for wheels, now behind the uniform construction
protocol above; a bootstrap/translation shim for external providers. An adapter references exactly
one provider. An adapter with state of its own is a provider and must be declared as one — a
self-declaration, and worth naming as such: v3 backed the equivalent claim with its
shared-dependency allowlist and exclusive-primary rules, and §4 rules 7 and 8 are what replace
that backing.
<!-- claim: v4-adapters-own-no-state -->

### 3.5 Authority binding — the active canonical provider for a capability and scope

`(capability, scope) → provider` plus the adapter used to reach it. This is where the v3
exclusive-primary guarantee lives now:

- for an `exclusive` capability there is **at most one** binding per scope. Not "exactly one":
  the validator cannot enumerate the instances of a `project` or `environment` scope, so a
  per-instance existence rule is unenforceable. Existence is governed by `required` (§3.2),
  which is checked only where the profile declares the instance;
  <!-- claim: v4-single-authority-per-scope -->
- a binding references a provider **by release unit**, and a release unit backs at most one
  `exclusive` binding unless the profile declares the coupling explicitly. That explicit
  declaration is what replaces v3's structural guarantee: v3 rejected sharing unconditionally,
  so an earlier draft's rule — resting only on the frozen/iterative distinction — left two
  iterative exclusive capabilities free to co-repin silently, which is exactly the class v3
  forbids. Coupling is now visible rather than absent;
  <!-- claim: v4-federation-cannot-repin-execution -->
- `multi` capabilities carry any number of bindings; `projection` bindings are never selected
  as an authority by any caller.
  <!-- claim: v4-multi-sinks-legal -->

v3's guarantee was never absolute and this freeze should not claim it was: `composition.py:399-402`
deliberately lets a *shared* dependency be referenced by many descriptors, and the reference
composition uses that — repinning `vuoro-adapter-kit` today repins every role referencing it, at
once. The honest statement is that no two roles share an adapter or owner-dependency lock, while
shared Vuoro-owned dependencies are deliberately co-pinned. v4 keeps that: shared providers are
declared, and §4 rule 8 carries the constraint that made them safe.

Authority is a *binding* fact, not a provider fact. OpenBao is bound to `secret.lease/v1`; it is
not "the authority plane".

### 3.6 Composition profile — a tested set of exact pins

Two documents, deliberately distinct:

- **Support manifest**: compatible contracts with version ranges. This is what Vuoro *supports*.
- **Reference profile lock**: exact provider, adapter, schema/migration, configuration-digest and
  protocol-version pins that were actually tested together, plus the conformance and migration
  report. This is what Vuoro *tested*.

For every provider the profile lock records the **deployment closure**: artifact identity,
adapter identity, effective configuration/policy digest, schema and migration version, protocol
version, and the probe evidence record. An unchanged image with a changed configuration or schema
is a changed closure and must re-validate.
<!-- claim: v4-closure-not-just-image -->

`migrated_from`, when a profile supersedes one, is
`{schema_version: "vuoro-composition/v3", manifest_sha256: "<hex>"}` — the sha256 of the
predecessor manifest's bytes, which is the only whole-profile identity v3 has and is already
bound by the installed-composition attestation (`scripts/attest_installed_composition.py:219`,
verified at `composition.py:471`). v4 carries the same shape forward as `profile_sha256` on the
profile itself, so successors have an in-band anchor.

Four profiles, defined here because an earlier draft introduced the names and left them
undefined. They are deployment shapes, not the code's environment classes and not the README's
modes, and the migrated v3 reference lock belongs to `shared`:

| Profile | Meaning |
| --- | --- |
| `local` | single operator machine; owner services in-process or on localhost; break-glass path |
| `shared` | the homelab cluster: owner services deployed, one Vuoro serving them — where the v3 reference composition lands |
| `served` | Vuoro served over the network to clients that are not co-located; probe conformance rather than in-process |
| `cloud` | externally hosted providers bound for one or more contracts |

Profiles may bind different providers to the same contracts. The contracts, cardinalities and
validator are profile-independent; only bindings vary.

## 4. The validator

One validator, one source of truth. The required-contract set is read from the support manifest,
never from a constant, and the fetch/attest/startup scripts consume the validator's output rather
than re-deriving it. It proves, per profile:

1. every `exclusive` contract has **at most one** binding per scope, every `required` contract is
   bound in each scope instance the profile declares, and every binding targets a declared
   provider;
2. every provider's deployment closure is complete and digest-bound;
3. conformance evidence exists per the contract's `conformance` kind — operation hashes for wheels
   (today's `validate_released_*_adapter.py`), a probe record for external providers;
4. frozen contracts' operation hashes equal the frozen baseline;
5. no adapter declares canonical state;
6. `migrated_from`, when present, resolves to a predecessor profile and carries an equivalence
   proof (§5). There is deliberately **no** rule that a profile's digest must differ from its
   predecessor's: the digest is a sha256 over the canonical profile serialization, so distinct
   profiles are distinct by construction, and a "must differ" rule would be either tautological
   or a demand for a synthetic bump on a genuinely no-op migration;
7. every adapter satisfies the uniform construction protocol (§3.3), and no module under
   `scripts/` or in the service package contains a contract-name literal set or an owner-specific
   module path. This is the rule the whole freeze exists for, so it is stated as a rule and
   falsified as one;
   <!-- claim: v4-declarative-contract-set -->
8. **carried forward from v3, generalised.** Artifact identity is unique within the fetch
   namespace (v3 rejected colliding artifact filenames because `verify_adapter_artifacts` stages
   every artifact into one directory, `composition.py:337-342`, `:419-437`); an owner dependency
   shares its primary's source repository (`:369-374`); a shared dependency comes from the
   canonical Vuoro repository and an allowlisted distribution (`:375-384`) — the mechanised form
   of §2's thinness claim; every declared provider is reachable from some binding, with no
   orphans (`:403-405`); and wheel artifacts keep v3's canonical release-URL provenance rule
   (`_release_wheel_identity`, `:92-130`), with `image`/`chart`/`binary` requiring a stated
   registry or origin allowlist, because a digest binds content and not provenance;
9. `_DEPLOYABLE_ENVIRONMENT_CLASSES` is carried forward as the enumeration that `environment`-scoped
   contracts bind against (`composition.py:40`, `:853`).

## 5. Migration from v3

**The served global catalog revision is preserved, and that is the goal.** An earlier draft said
the opposite — that v4 "necessarily changes it" — which inverted the fact.
`CatalogRegistry.revision` is a sha256 over the registered operation definitions plus resource
kinds and observation transports (`catalog.py:361-384`); nothing about a manifest, lock, pin or
profile is an input to it. So a migration that rebinds the same adapters is invisible on the wire.

That matters because a revision change is not a cache miss, it is a fleet-wide cutover: it is
served as an ETag (`app.py:246`) and on every result (`app.py:222`), and a mismatch is rejected
with `409 stale-catalog` before the operation is even resolved (`app.py:276-283`), with the
downstream contract stating Vuoro never transparently retries a mutation. ActionQ's freeze uses
the identical object and expects it to change at W4 because W4 *adds operations* — not because a
manifest was reserialized.

What is **not** preserved is the manifest serialization and therefore the profile digest.

The current reference composition (`adapter-pins.json`, 7 locks, 4 descriptors) maps losslessly:

| v3 | v4 |
| --- | --- |
| `release_locks[*]` (7) | providers, `artifact_kind: wheel`, roles as today |
| `runtime_descriptors[*].domain` | plane label only (render-time) |
| `runtime_descriptors[*].api_version` | the capability contract id |
| `runtime_descriptors[*].{lock_id, adapter_module, register}` | adapter record, behind the uniform construction protocol (§3.3) |
| `runtime_descriptors[*].dependency_lock_ids` | the provider's declared dependency closure, carried by §4 rule 8 |
| implicit 1:1 descriptor↔lock | explicit `exclusive` authority binding, scope `global` |
| `schema_version` per descriptor | part of the provider's deployment closure |
| `_REQUIRED_DOMAINS` | support manifest's required-contract list |

`dependency_lock_ids` is called out because an earlier draft's mapping dropped it, which would
have made "maps losslessly" false in a way the equivalence proof could not see: it is the edge
binding the three non-adapter locks into the composition, and without a v4 home the invariants at
`composition.py:376-405` would vanish while a proof over providers, digests, operation hashes and
bindings still passed.

What is preserved, and proven equal by the equivalence proof: component pins and hashes,
`execution/v1` operation hashes, every binding (same provider for the same capability), the
declared dependency closure, and the observable served catalog — including its revision, byte for
byte. What is **not** preserved is the manifest serialization and therefore the profile digest;
the v4 profile records `migrated_from` (§3.6) and the proof.
<!-- claim: v4-lossless-semantic-migration -->

## 6. Proof plan

The ontology is proven against three deliberately different providers before federation is
rescoped:

| Case | Provider | What it exercises |
| --- | --- | --- |
| internal authoritative | ActionQ `execution/v1` | wheel, operation-hash conformance, frozen contract, exclusive binding, migration equivalence |
| external authoritative | OpenBao `secret.lease/v1` | image + closure, probe conformance, exclusive binding to a narrow capability |
| non-authoritative multi | OTel → Prometheus | `multi` export bindings, `exclusive` storage binding, `projection` analytics |

Then: lossless migration of the full v3 composition (§5). Only then federation and W4.

## 7. Identity carried into the authority plane

The W4 scope review's largest open item moves here unchanged: federation ownership equality
turns on `principal_id` forever and v1 has no ownership transfer. A `federation.principal/v1`
contract must therefore forbid a reissued actor identity from acquiring a historical principal's
ownership. This is a contract-level claim and is a prerequisite of any federation provider binding.

**Settled 2026-08-22, and implemented on the issuer side.** `principal_id` is a mint-once
identifier carried by the identity assertion — `<issuer>:<subject>:<epoch>`, the epoch
incrementing on any reissue — and is a separate field from the display `actor`, which is chosen
for humans and expected to change. A reissued actor is therefore a different principal by
construction and inherits nothing, so v1 needs no ownership-transfer operation to be correct.
`Identity.principal_id`, the gateway assertion's required `principal_epoch` claim and the static
registry's required `principal_id` are the issuer half; minting and persisting the per-subject
epoch is Vuoro Cloud's. Reserved system principals (ActionQ's pinned `federation-backfill/v1`)
are exempt by name. The rescope that consumes this is actionq #41.
<!-- claim: v4-reissued-identity-no-ownership -->

## 8. Sequence and status

1. ActionQ PR #40 is paused as architecturally superseded; its review corrections, measurements
   and falsifier-instrument findings are retained.
2. This freeze is reviewed through both channels (PR gate + design session) like W3.
3. Vuoro candidate: v4 loader and validator beside v3, reference profile migrated, the three proof
   cases, the falsifier gate (`tests/test_falsifier_coverage.py` from actionq) ported so that §9 is
   enforced here rather than read.
4. Federation/W4 rescoped as authority-plane contracts; packaging follows ownership.

## 9. Falsifiers

Every marked claim above names the test that would fail if it were untrue. Eleven of the twelve
now do; the document declares `minimum_coverage: 0.9`, and the number moved because the candidate
landed, which is what it was there to show. The freeze was written at 0.0 with every entry a
declared gap — the gate's own encoding for that state — precisely so this movement would be
visible rather than asserted.

The twelfth, `v4-direct-recovery-without-vuoro`, stays a declared gap and is not expected to
close here: it needs sprintctl's and actionq's CLIs run against their own stores with
vuoro-service absent, and neither owner distribution is installed in this workspace. A test that
asserted something weaker while appearing to discharge the claim would be worse than the gap, so
the test that exists asserts only that the gap is real.

Scope strings are restated in each test's docstring — the gate binds them, so widening a claim
fails until someone edits the test — and no two claims share a scope.

```falsifiers
{
  "minimum_coverage": 0.9,
  "falsifiers": [
    {
      "id": "v4-declarative-contract-set",
      "claim": "The set of required contracts is read from the support manifest, and no service or script code carries a contract-name literal set.",
      "scope": "adding a contract to the support manifest changes no .py file, and no module under scripts/ or in the service package contains a contract-name literal set",
      "test": "tests/test_composition_v4.py::test_adding_a_contract_touches_no_python"
    },
    {
      "id": "v4-uniform-construction",
      "claim": "Every wheel provider is constructed through one protocol; no owner name, module path or registration convention appears in Vuoro service code.",
      "scope": "a profile declaring a contract whose adapter does not satisfy build/register is rejected, and composing it registers that contract's operations without any owner-specific branch",
      "test": "tests/test_composition_v4.py::test_new_contract_composes_without_service_code_change"
    },
    {
      "id": "v4-direct-recovery-without-vuoro",
      "claim": "Removing Vuoro does not prevent direct or local recovery of owner capabilities.",
      "scope": "with the vuoro-service package absent, sprintctl and actionq direct CLIs complete their documented break-glass paths against their own stores",
      "test": null,
      "gap": "Still a gap, and not one this repository can close: the claim needs sprintctl's and actionq's CLIs run against their own stores with vuoro-service absent, and neither owner distribution is installed in this workspace. tests/test_composition_v4.py::test_owner_capabilities_recover_without_vuoro asserts only that the gap is real.",
      "planned_test": "tests/test_composition_v4.py::test_owner_capabilities_recover_without_vuoro"
    },
    {
      "id": "v4-cardinality-is-per-capability",
      "claim": "Cardinality belongs to the capability, never to the plane or the provider.",
      "scope": "a cardinality declared anywhere but on a capability contract is rejected, and required is accepted independently of cardinality",
      "test": "tests/test_composition_v4.py::test_cardinality_is_rejected_outside_capability_records"
    },
    {
      "id": "v4-independent-release-units",
      "claim": "Two authoritative capabilities that must evolve independently are separate release units even from one repository.",
      "scope": "two providers sharing source_repository but differing in release unit are accepted, and one provider record bound to two frozen-and-iterative capabilities is rejected",
      "test": "tests/test_composition_v4.py::test_frozen_and_iterative_capabilities_need_separate_release_units"
    },
    {
      "id": "v4-adapters-own-no-state",
      "claim": "An adapter owns no canonical state.",
      "scope": "an adapter record declaring a schema or store is rejected; the same declaration on a provider record is accepted",
      "test": "tests/test_composition_v4.py::test_adapter_records_cannot_declare_canonical_state"
    },
    {
      "id": "v4-single-authority-per-scope",
      "claim": "No scope has two active canonical authorities.",
      "scope": "two authority bindings for one exclusive capability and one scope are rejected, across every profile",
      "test": "tests/test_composition_v4.py::test_no_scope_has_two_authorities"
    },
    {
      "id": "v4-federation-cannot-repin-execution",
      "claim": "Changing the federation provider cannot repin frozen execution/v1.",
      "scope": "a profile binding federation and execution/v1 to the same provider release unit, without an explicit coupling declaration, is rejected by the validator",
      "test": "tests/test_composition_v4.py::test_federation_change_leaves_execution_v1_binding_identical"
    },
    {
      "id": "v4-multi-sinks-legal",
      "claim": "Multiple observability sinks remain legal.",
      "scope": "two or more bindings on a multi-cardinality telemetry.export capability validate",
      "test": "tests/test_composition_v4.py::test_multiple_telemetry_exporters_validate"
    },
    {
      "id": "v4-closure-not-just-image",
      "claim": "A provider update cannot pass using only an unchanged image while configuration or schema changed.",
      "scope": "an unchanged image digest with a changed configuration digest or schema version fails validation until the closure is re-attested",
      "test": "tests/test_composition_v4.py::test_unchanged_image_with_changed_closure_fails"
    },
    {
      "id": "v4-lossless-semantic-migration",
      "claim": "The v3 reference composition migrates to v4 with pins, operation hashes, bindings and observable semantics preserved.",
      "scope": "a CatalogRegistry built from the migrated v4 profile has a byte-identical revision to one built from the v3 manifest, and the migrated providers, sha256 values, execution/v1 operation hashes, bindings and dependency closure equal adapter-pins.json",
      "test": "tests/test_composition_v4.py::test_v3_reference_composition_migrates_losslessly"
    },
    {
      "id": "v4-reissued-identity-no-ownership",
      "claim": "A reissued actor identity cannot acquire historical ownership.",
      "scope": "the federation.principal/v1 contract declares ownership non-transferable and no bound provider passes conformance without evidence for it",
      "test": "tests/test_composition_v4.py::test_reissued_identity_owns_nothing_historical"
    }
  ]
}
```

## 10. Decisions taken in this freeze

- Federation is three contracts, split because they differ in `frozen` status and scope —
  §3.2. **The split itself is settled; the per-contract owner and scope assignments are
  proposed and need owner confirmation**, because ActionQ implements no principal registry,
  partitions principals by environment rather than globally, and has no project dimension for
  grants. Confirming or correcting those three is a W4-rescoping input, not a blocker on this
  freeze.
- `knowledge/v1` (kctl) is in the evidence and provenance plane: knowledge artifacts are
  retained provenance, not telemetry. Plane placement is render-only, so this costs nothing to
  revisit.
- `scope_kind`: `secret.lease/v1` and `metrics.storage/v1` are per `environment` (matching the
  deployable environment classes v3 already enforces); `policy.decision/v1` is `global`.

## 11. Amendments

Frozen rules change only here. An amendment is **proposed** until the owner ratifies it; a
proposed amendment authorizes no code change.

### Proposed Amendment 2 — rule 8 filename→digest refinement (pending owner decision D-6)

Status: **proposed, not ratified** (2026-08-23). Source: requirements pathway
`2026-08-23-requirements-pathway-v5-v7.md` R1.3.1, obstacle O6, decision D-6 (default: accept).
Needed by actionq W5 item 5.4 (second ActionQ provider record for `federation.resource/v1`).

**Problem.** Rule 8 as frozen makes artifact identity the *filename* within the fetch namespace:
`composition.py:342-347` rejects any two release locks whose release-URL identity yields the same
wheel filename, and `verify_adapter_artifacts` (`:428-433`) rejects the same at staging. A second
ActionQ provider record binding `federation.resource/v1` from the same released wheel
(`actionq-0.1.28-py3-none-any.whl`) as the `execution/v1` provider therefore cannot be declared at
all, although both records name one identical artifact with one identical digest. That is not a
collision in the sense rule 8 exists to prevent (two different artifacts overwriting each other in
one staging directory); it is one artifact referenced twice.

**Refinement.** The collision check compares **filename → digest** within the fetch namespace and
rejects only *conflicting* digests: two locks naming the same filename with the same sha256 are
the same artifact and are accepted; the same filename with differing digests remains a collision
and is rejected exactly as today. Everything else in rule 8 (owner-dependency repository rule,
shared-dependency allowlist, no orphan providers, release-URL provenance, registry allowlists) is
unchanged.

**Where it is enforced (when ratified).**

- `packages/vuoro-service/src/vuoro_service/composition.py`: the manifest-level check at
  `:342-347` builds `{filename: digest}` and raises `CompositionError` only when a filename maps
  to more than one digest; the staging check in `verify_adapter_artifacts` (`:428-433`) keeps a
  `{filename: digest}` map instead of a `set[str]`, stages the artifact once, and raises
  `artifact filename collision` only on a digest conflict. The fetch script's equivalent
  check (`test_fetcher_rejects_dependency_filename_collisions`,
  `tests/test_adapter_dependencies.py:71`) follows the same rule.
- Tests to add, same package: (a) two release locks, identical filename and digest → manifest
  loads and staging succeeds with one staged file; (b) identical filename, differing digests →
  still rejected by both the manifest check and `verify_adapter_artifacts`; (c) the existing
  collision tests keep passing unchanged, since they use differing artifacts.
- Rule 1 is untouched: the validator must still reject one release unit backing both
  `exclusive` capabilities in one scope (W5 5.4 restates this as its own gate); this amendment
  only lets two *provider records* share one *artifact*.

**Why this is an amendment and not a bug fix.** Rule 8 was carried forward from v3 verbatim and
the filename rule is stated in the frozen text (§4 rule 8, `composition.py:337-342`), so the
change is a change to a frozen rule and is recorded as such. It does not alter any correctness
rule (evidence binding, exclusivity, no-sole-attester) and is not loosened by C-1 — it is asked
for on its own merits.

(Amendment 1 is reserved for the per-contract owner/scope confirmations listed in §10, which the
W4 rescope took as its input; they are recorded there, not here.)
