---
doc_id: vuoro-composition-v4-design-freeze-2026-08-22
status: proposed
supersedes:
  - actionq freeze doc W4 "exactly five descriptors" (2026-08-20-tranche4-federation-storage-contract-freeze.md:501-511)
  - actionq PR #40 option 2 "second ActionQ distribution" (superseded, not wrong)
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
harness adapters — per the 2026-08-20 alignment), any change to owner-side contracts, and the
rescoping of ActionQ federation/W4, which follows this freeze rather than preceding it.

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

Six distinct objects. Each is a separate record type in the manifest; none is inferred from
another.

### 3.1 Plane — operator-facing grouping only

| Plane | Representative capabilities | Today's providers (non-binding) |
| --- | --- | --- |
| Work and tracking | task definition, dependencies, readiness, acceptance, advisory reservations | sprintctl (`work-api/v1`) |
| Coordination | run requests, reconciliation, session lineage, external-execution references | ActionQ federation (external-execution references, assurance, reconciliation) |
| Execution | product-native runtimes (Claude Code, Codex, OpenCode, Copilot), ACP, worktrees | external, qualified per `docs/evidence/2026-08-19-native-harness-qualification.md`; no Vuoro-owned runner |
| Authority and policy | identity, principals, grants, policy decisions, credential/secret leases | federation principals and grants; OPA (decisions); OpenBao (secret leases); cred-broker |
| Evidence and provenance | command capture, manifests, knowledge artifacts, audit ledger, retention | auditctl (`audit/v1`), kctl (`knowledge/v1`), federation retention export (W3) |
| Observability and analytics | telemetry collection, metrics, traces, dashboards, derived analytics | OTel collectors → Prometheus; homelab-analytics |

Evidence is first-class and is **not** folded into observability: telemetry may be lossy,
provenance may not. A plane carries no cardinality, no authority and no validation rule.
Nothing in the validator references a plane except to render reports.

Reconciliation with the 2026-08-20 alignment: "placement" and "leases" appear in the
coordination plane only as *capabilities that may have no provider*. ActionQ's queue, claims,
leases and runner are deletion targets; `actionq-dispatcher` is retired; Restate is an
unstarted pilot and is not a packaged component. Listing a capability is not a commitment to
provide it.

### 3.2 Capability contract — the smallest versioned semantic interface

Identifier `<name>/v<N>`. The existing v3 `api_version` strings are already capability ids and
are carried forward unchanged: `work-api/v1`, `execution/v1`, `knowledge/v1`, `audit/v1`.

Each contract declares:

- `cardinality` — one of:
  - `exclusive`: exactly one active authority per scope;
  - `multi`: several simultaneous providers (telemetry exporters);
  - `optional`: absence is valid;
  - `projection`: derived, read-only, never authoritative.
- `scope_kind` — what an authority binding is exclusive *over* (`tenant`, `project`,
  `environment`, `global`).
- `frozen` — whether the contract's operation bytes/hashes are pinned for the lifetime of the
  profile (`execution/v1` is frozen; federation contracts are iterative).
- `conformance` — how a provider proves it: `operation-hashes` (internal wheels, as today) or
  `probe` (external providers, as `validate_served_conformance.py` already does for served mode).

Cardinality belongs to the capability, never to the plane or the provider.
<!-- claim: v4-cardinality-is-per-capability -->

Initial contracts beyond the four carried forward. Federation is **three** contracts, not one: identity is frozen forever, grants iterate, and the resource ledger has a different scope from both — one contract would force a single frozen/iterative status and a single scope onto three things that differ in exactly those properties. Contract ids are settled here; their operation sets are the owners' to freeze:

| Contract | Cardinality | Owner | Note |
| --- | --- | --- | --- |
| `federation.principal/v1` | exclusive, scope `global`, frozen | ActionQ | identity is forever (§7); a principal id must not depend on environment or project |
| `federation.grant/v1` | exclusive, scope `project` | ActionQ | ACL; iterates independently of identity |
| `federation.resource/v1` | exclusive, scope `environment` | ActionQ | the append-only ledger (W3); matches the W3 identity namespace `(mapping_version, environment, source_id, id)` |
| `secret.lease/v1` | exclusive, scope `environment` | OpenBao | mechanics only — not identity, grants or decisions; one vault per deployable environment class |
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
<!-- claim: v4-independent-release-units -->

### 3.4 Adapter — a thin Vuoro translation layer that owns no canonical state

The v3 descriptor's `adapter_module` + `register` for wheels; a bootstrap/translation shim for
external providers. An adapter references exactly one provider. An adapter with state of its own
is a provider and must be declared as one.
<!-- claim: v4-adapters-own-no-state -->

### 3.5 Authority binding — the active canonical provider for a capability and scope

`(capability, scope) → provider` plus the adapter used to reach it. This is where the v3
exclusive-primary guarantee lives now:

- for an `exclusive` capability there is at most one binding per scope;
  <!-- claim: v4-single-authority-per-scope -->
- a binding references a provider **by release unit**, so changing the federation provider cannot
  change the `execution/v1` binding, even if both are built from the actionq repository;
  <!-- claim: v4-federation-cannot-repin-execution -->
- `multi` capabilities carry any number of bindings; `projection` bindings are never selected
  as an authority by any caller.
  <!-- claim: v4-multi-sinks-legal -->

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

Profiles `local`, `shared`, `served`, `cloud` may bind different providers to the same contracts.
The contracts, cardinalities and validator are profile-independent; only bindings vary.

## 4. The validator

One validator, one source of truth. The required-contract set is read from the support manifest,
never from a constant, and the fetch/attest/startup scripts consume the validator's output rather
than re-deriving it. It proves, per profile:

1. every `exclusive` contract in the support manifest has exactly one binding per declared scope,
   every `optional` one has at most one, and every binding targets a declared provider;
2. every provider's deployment closure is complete and digest-bound;
3. conformance evidence exists per the contract's `conformance` kind — operation hashes for wheels
   (today's `validate_released_*_adapter.py`), a probe record for external providers;
4. frozen contracts' operation hashes equal the frozen baseline;
5. no adapter declares canonical state;
6. the profile's global revision differs from the one it supersedes, and `migrated_from` (if
   present) carries an equivalence proof (§5).

## 5. Migration from v3

The current reference composition (`adapter-pins.json`, 7 locks, 4 descriptors) maps losslessly:

| v3 | v4 |
| --- | --- |
| `release_locks[*]` (7) | providers, `artifact_kind: wheel`, roles as today |
| `runtime_descriptors[*].domain` | plane label only (render-time) |
| `runtime_descriptors[*].api_version` | the capability contract id |
| `runtime_descriptors[*].{lock_id, adapter_module, register}` | adapter record |
| implicit 1:1 descriptor↔lock | explicit `exclusive` authority binding, scope `global` |
| `schema_version` per descriptor | part of the provider's deployment closure |
| `_REQUIRED_DOMAINS` | support manifest's required-contract list |

What is preserved, and proven equal by the equivalence proof: component pins and hashes,
`execution/v1` operation hashes, every binding (same provider for the same capability), and the
observable served catalog. What is **not** preserved: the serialization, and therefore the global
revision — v4 necessarily changes it. The v4 profile records `migrated_from: <v3 global revision>`
and the proof, rather than pretending the revision is unchanged.
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

Every marked claim above names the test that would fail if it were untrue. None of these tests
exist yet: **this block is the red test list for the candidate**, and the ported gate is expected
to fail on this document until the candidate lands. Scope strings must be restated in each test's
docstring; no two claims may share a scope.

```falsifiers
[
  {
    "id": "v4-direct-recovery-without-vuoro",
    "claim": "Removing Vuoro does not prevent direct or local recovery of owner capabilities.",
    "scope": "with the vuoro-service package absent, sprintctl and actionq direct CLIs complete their documented break-glass paths against their own stores",
    "test": "tests/test_composition_v4.py::test_owner_capabilities_recover_without_vuoro"
  },
  {
    "id": "v4-cardinality-is-per-capability",
    "claim": "Cardinality belongs to the capability, never to the plane or the provider.",
    "scope": "a manifest declaring cardinality on a plane or provider record is rejected by the loader",
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
    "scope": "replacing the federation provider's artifact leaves the execution/v1 binding, artifact sha256 and operation hashes byte-identical",
    "test": "tests/test_composition_v4.py::test_federation_change_leaves_execution_v1_binding_identical"
  },
  {
    "id": "v4-multi-sinks-legal",
    "claim": "Multiple observability sinks remain legal.",
    "scope": "two or more bindings on a multi-cardinality telemetry.export capability validate, while two bindings on exclusive metrics.storage do not",
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
    "scope": "the migrated profile's providers, sha256 values, execution/v1 operation hashes and capability bindings equal the v3 adapter-pins.json, the global revision differs, and migrated_from records the v3 revision",
    "test": "tests/test_composition_v4.py::test_v3_reference_composition_migrates_losslessly"
  },
  {
    "id": "v4-reissued-identity-no-ownership",
    "claim": "A reissued actor identity cannot acquire historical ownership.",
    "scope": "a principal record reissued for an existing actor string receives a new principal_id and owns no prior federation resource",
    "test": "tests/test_composition_v4.py::test_reissued_identity_owns_nothing_historical"
  }
]
```

## 10. Decisions taken in this freeze

- Federation is three contracts (`principal` global/frozen, `grant` project, `resource`
  environment) — §3.2.
- `knowledge/v1` (kctl) is in the evidence and provenance plane: knowledge artifacts are
  retained provenance, not telemetry. Plane placement is render-only, so this costs nothing to
  revisit.
- `scope_kind`: `secret.lease/v1` and `metrics.storage/v1` are per `environment` (matching the
  deployable environment classes v3 already enforces); `policy.decision/v1` is `global`.
