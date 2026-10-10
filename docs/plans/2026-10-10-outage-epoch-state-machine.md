---
doc_id: vuoro-outage-epoch-state-machine
title: "Outage semantics: NORMAL / DEGRADED LOCAL / RECOVERY for vuoro-shared"
purpose: specification
lifecycle: proposed
effective: 2026-10-10
applies_to:
  components: [vuoro-core, sprintctl]
related: [vuoro-disposition-register]
---

# Outage semantics: NORMAL / DEGRADED LOCAL / RECOVERY

Served item: agentops#2537 (operator decision on agentops#255, 2026-09-27).
Architecture: `agentops:docs/architecture/2026-09-27-agentic-ecosystem-and-split-horizon.md`
§3.3 and Q3. The state machine is frozen there. This document specifies its
contract. It is a design document with no behaviour code.

## 1. Problem

vuoro.cloud is the primary coordination plane. The homelab must keep
working when vuoro.cloud is unreachable. Once the fallback can write, two
histories exist unless recovery is defined. The architecture rejects
active-active dual writing. It frames `vuoro-shared` as a **protected
substrate + emergency coordination island**, not a peer backend (Q3). The
disposition register adds that "the DEGRADED LOCAL island is the same
sprintctl on vuoro-shared under an outage epoch … not a second
implementation" (`docs/direction/disposition-register.yaml:665`).

Facts that shape the contract:

- **What vuoro-shared runs.** `vuoro-shared` runs the `vuoro-service` image
  (release v0.1.93), which binds only the work and audit domains
  (`appservice:clusters/main/kubernetes/apps/vuoro-shared/app/deployment.yaml:8-11`).
- **Item ids.**
  - Integer item ids are per-deployment sequences
    (`sprintctl/pg.py:106-234`).
  - The item aggregate UUID (random uuid4, UNIQUE) is the only identifier
    that is collision-safe across deployments
    (`sprintctl/db.py:488-504`).
- **Audit ids.** auditctl event ids (`ad:` + ULID-like) and `origin_stream_id`
  (uuid4) + `origin_seq` are globally unique (`auditctl/ids.py:15-24`).
- **Conflict signals already exist.** Revisions are CAS-protected:
  - `item-edit-conflict`, stale `status_revision`
  - `claim-superseded`, `idempotency-conflict`
  - see `sprintctl:docs/reference/vuoro-work-adapter.md:22-62,85,222`
- **Clients have one endpoint.** A client profile names exactly one endpoint
  and has no mode or fallback field
  (`vuoro-client/src/vuoro_client/profile.py:30-56`).
- **Freeze exists, and this design does not use it.** vuoro.cloud's
  `mutations_frozen` is a single global `service_controls` row
  (`vuoro-cloud:src/vuoro_cloud/gateway.py:202-211`, `lifecycle.py:706-722`).
  It sits under "OIDC + explicit operator step-up" (split-horizon Q2), with a
  tunnel-plane twin that requires a touch and is lifted only on the tunnel
  plane (admin-identity design C2, A3). No workload identity holds it.
  Freezing it would also freeze every tenant for one homelab island.
- **Import surface.** vuoro.cloud has no import or bulk-ingest surface today.
- **Today is not NORMAL.** Until the Q1(b) cutover, vuoro-shared is
  authoritative for work items (split-horizon §3.3 "Transition"). The state
  machine starts at the cutover.

## 2. Decision

1. **No change is ever written to both stores, and none is merged
   silently.**
   - In NORMAL, vuoro.cloud is the only writable store for work records.
   - In DEGRADED LOCAL, the island accepts epoch-tagged writes.
   - vuoro.cloud is not fenced: hosted runtimes that can still reach it may
     keep writing.
   - At import, an island change reaches vuoro.cloud only as a CAS write
     against the `base_revision` it was made on. Whenever the cloud moved in
     the meantime, the cloud value stays and the island change becomes a
     conflict record.
   - The mode record on the protected side says which state holds.
2. **Entering DEGRADED LOCAL is an explicit operator act**, as the frozen
   architecture requires.
   - It is a declaration, not an approval gate. One protected-side command
     opens an outage epoch, and nothing else waits on the operator.
   - The command records a cheap check of fact with the epoch (a vuoro.cloud
     health probe result) but does not refuse on it. The operator may enter
     on a partial outage.
3. **The epoch tags every record written on the island.**
   - Epoch identifier: `oe:<ULID>`.
   - Every work and audit write accepted in DEGRADED LOCAL carries
     `outage_epoch` and the `base_revision` it was written against (the
     cached cloud revision of the item, or `null` for a new item).
4. **No cloud-side freeze.** Island mode never sets or lifts
   `mutations_frozen`.
   - That switch is global and operator-only by a recorded decision (§1).
     Using it would either put the operator back into the recovery path or
     amend #255 Q2.
   - Neither is needed. The `base_revision` CAS at import already catches
     every cloud write made during the epoch, item by item.
   - An epoch-scoped work-write fence, settable by `vuoro-ops`, is a
     possible later hardening. It would need a recorded amendment to Q2 and
     the admin-identity design, and it is not part of this contract.
5. **Exit is a declaration, like entry, and RECOVERY is automatic after
   it.**
   - The architecture fixes only that entry is an explicit operator act
     (§3.3). Exit is made one too, for the same flap reason: if exit fired
     on a recovered health probe, a flapping probe would seal the epoch while
     vuoro.cloud is still unusable, and each flap would cut a new epoch with
     its own import.
   - Exit is one protected-side command. The island keeps working until it
     runs, so leaving it to a declaration costs nothing.
   - A cheap check stands in for an approval: while an epoch is open and the
     recorded cloud probe has been healthy for more than 6 h, the island
     appends an `island.overdue` notice to its mode record and readiness
     view. The notice blocks nothing.
   - The command `island exit` seals the epoch: island work writes stop, and
     a digest-bound export bundle is produced.
   - The protected side pushes the bundle outbound to vuoro.cloud. Pull-only
     direction is preserved: the protected side opens the connection.
   - vuoro.cloud imports it idempotently by `epoch_id` and `bundle_digest`.
   - Authority returns to vuoro.cloud. There is no freeze to lift.
   - No step after `exit` waits on the operator.
6. **Conflicts are surfaced, never silently merged, and never block the
   import.**
   - A conflicting island change is not applied. It becomes an
     `island.conflict` record attached to the item, which carries both
     revisions.
   - Non-conflicting changes apply.
   - Conflicts are routed like any refinement work: a coordinator session
     resolves each one with a recorded resolution. The operator is not the
     default resolver.

## 3. Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Active-active dual writing, or automatic failover to vuoro-shared | Rejected by the operator decision (§3.3, Q1(c)). It produces two histories with no defined merge. |
| Automatic entry on failed health probes | Contradicts the frozen "explicit operator act". A flapping probe would split history. |
| Automatic exit on a recovered health probe | It has the same flap problem in reverse (§2.5). The `island.overdue` notice covers a forgotten exit without acting on it. |
| Freeze vuoro.cloud (`mutations_frozen`) during the epoch | It is global across tenants and held only by the operator with step-up or touch (#255 Q2, admin-identity C2/A3). An automatic freeze or unfreeze would contradict that placement, and a manual one would put the operator into recovery. The CAS at import makes it unnecessary. |
| Last-writer-wins merge at import | It is a silent merge, which the architecture forbids. |
| Block the whole import until every conflict is resolved | That would make the operator, or a resolver, a gate on returning authority. Per-item conflict records cost nothing, and authority can return immediately. |
| Integer item ids as import keys | They collide across deployments. The aggregate UUID is the key, and integer ids are remapped. |
| A second sprintctl implementation for the island | The register says the island is the same sprintctl under an epoch. Epoch tagging is an owner field, not a fork. |
| Operator approval of each conflict resolution | An artificial gate. A resolution is a recorded CAS edit that anyone can audit, and that check is the cheap one. |

## 4. Contract

### 4.1 States and transitions

```text
            island enter (operator, protected side)
NORMAL ─────────────────────────────────────────────▶ DEGRADED_LOCAL(oe)
  ▲                                                      │
  │ import receipt digest == bundle digest               │ island exit (operator)
  │ (automatic)                                          ▼
  └───────────────────────────────────────────────── RECOVERY(oe)
                                                     sealed → exported → imported
```

| State | vuoro.cloud work writes | vuoro-shared work writes | Reads |
|---|---|---|---|
| `NORMAL` | authoritative | refused `island-not-active` (409) | both. The island serves its cached read model and labels it as cached. |
| `DEGRADED_LOCAL` | not fenced. Writes from callers that can reach it are accepted, and each one is a conflict candidate for island changes to the same item. | accepted, tagged `outage_epoch`, `base_revision` | both |
| `RECOVERY` | not fenced. Writes accepted before the import reaches an item are conflict candidates exactly as in DEGRADED_LOCAL. After the import, the cloud is authoritative. | refused `island-sealed` (409) | both |

RECOVERY has sub-phases: `sealed` (writes stopped, export bundle written),
`exported` (bundle pushed, awaiting receipt), and `imported` (receipt
matches). The machine reaches NORMAL from `imported` automatically. A failed
push retries with backoff. No phase waits on a person.

### 4.2 Mode record (protected side, append-only)

`island_mode` events on vuoro-shared. The current mode is the newest event.

| Field | Type | Notes |
|---|---|---|
| `event` | `entered` \| `overdue` \| `sealed` \| `exported` \| `imported` \| `returned` | |
| `epoch_id` | `oe:<ULID>` | Fixed at `entered`. |
| `actor_principal` | principal_id | Operator principal for `entered` and `sealed`; `vuoro-ops` workload principal for the rest. |
| `reason` | string | Required on `entered`. |
| `cloud_probe` | `{url, status, observed_at}` | Recorded on `entered`; never a refusal condition. |
| `base_watermark` | `{cloud_cursor, cached_at}` | Age of the island's cached read model at entry. |
| `bundle_digest` | `sha256:` | On `sealed` and `exported`. |
| `receipt` | `{import_id, bundle_digest, applied, conflicts}` | On `imported`. |

Who may act: `entered` and `sealed` are protected-only operator acts
(admin-identity design C3: recovery operations are protected-only). They run
as `vuoro-service island enter|exit` from the workstation or tunnel plane,
never through a public sign-in. The remaining transitions, including
`overdue`, run as the `vuoro-ops` workload identity.

### 4.3 Epoch-tagged record fields (owner: sprintctl)

Every work write accepted in DEGRADED_LOCAL adds two fields to the event
payload and item metadata:

- `outage_epoch: "oe:…"`
- `base_revision`: the item's cached `edit_revision` and `status_revision`
  at the time of the write, or `null` for an item created during the epoch

Audit events add `outage_epoch` and keep their own globally unique ids.
Items created during the epoch get a fresh aggregate UUID. Their integer id
is island-local and is remapped at import.

### 4.4 Export bundle

`vuoro-island-bundle/v1`: canonical JSON lines, sorted by
`(origin_stream_id, origin_seq)`, containing every epoch-tagged event, item
snapshot and audit event. `bundle_digest` is the sha256 of the canonical
bytes. The bundle carries `epoch_id`, `base_watermark` and the island's
`vuoro-service` release.

### 4.5 Import (owner: vuoro.cloud, consuming a sprintctl owner operation)

`work.island.import-v1` is a new owner operation in sprintctl, exposed by
the vuoro.cloud control plane on a protected-origin route. It accepts only
the `vuoro-ops` workload identity, with scope `vuoro:island.import`, over
the tunnel `/32` binding. It is not an MCP tool and not on the public
surface.

Per record, import is keyed by item aggregate UUID:

| Class | Condition | Result |
|---|---|---|
| `append` | events, notes, evidence entries, audit events | Appended with the epoch tag preserved. Both histories are kept. Never a conflict. |
| `create` | item created in the epoch (`base_revision: null`) | Created on vuoro.cloud with a new integer id. `island_id_map {epoch_id, island_id, cloud_id, aggregate_uuid}` is recorded. |
| `apply` | cloud's current revision == `base_revision` | Applied as a CAS write on the cloud side. |
| `conflict` | cloud's current revision != `base_revision` (an edit, status or claim happened on the cloud during the epoch) | Not applied. An `island.conflict` record is written (§4.6). The cloud value stays. |
| `lease` | an island lease on an item | Ended with `end_reason=island-epoch-closed`. Its run evidence is appended. A lease cannot be live on both sides. |

The import is idempotent on `(epoch_id, bundle_digest)`. A replay returns
the first receipt. A different digest for the same epoch is refused.

Receipt: `{import_id, epoch_id, bundle_digest, applied, created, appended,
conflicts: [conflict_id…]}`. The island transitions to `imported` only when
`receipt.bundle_digest` equals its own.

### 4.6 `island.conflict` record

| Field | Notes |
|---|---|
| `conflict_id`, `epoch_id`, `aggregate_uuid`, `item_id` (cloud) | |
| `field` | `description` \| `status` \| `lease` \| `other` |
| `island_change` | The full island event, unapplied. |
| `base_revision`, `cloud_revision` | |
| `state` | `open` → `resolved` |
| `resolution` | `{kind: keep_cloud \| apply_island \| edited, by_principal, at, note}` |

A resolution is an ordinary CAS write against `cloud_revision`. If the cloud
moved again, the resolution fails with the usual `item-edit-conflict` and
is retried. Open conflicts appear in readiness views as refinement work for
coordinator sessions. They never block other items or the return of
authority.

### 4.7 Client routing

Profiles gain `island_endpoint` (optional). A client sends work writes to
`target.endpoint` (vuoro.cloud). It reads the island mode only from the
island's read-only `work.island.mode-v1`. It switches writes to
`island_endpoint` only while that read returns `DEGRADED_LOCAL`. The
client never decides the mode itself. When the island refuses a write with
`island-not-active` or `island-sealed`, the client returns to
`target.endpoint`.

### 4.8 Errors

| Code | HTTP | Raised by | When |
|---|---|---|---|
| `island-not-active` | 409 | island | Work write in NORMAL. |
| `island-sealed` | 409 | island | Work write in RECOVERY. |
| `epoch-already-open` | 409 | island | `enter` while an epoch is open. |
| `epoch-not-open` | 409 | island | `exit` with no open epoch. |
| `import-digest-mismatch` | 409 | cloud | Same `epoch_id` with a different `bundle_digest`. |
| `import-identity-refused` | 403 | cloud | Not `vuoro-ops` over the tunnel binding. |
| `item-edit-conflict` | 409 | owner | Resolution against a stale cloud revision (existing code). |

## 5. Verification plan

Automated:

- `packages/vuoro-service/tests`, against disposable Postgres:
  1. NORMAL refuses island work writes.
  2. `enter` → writes are accepted and carry `outage_epoch` and
     `base_revision`.
  3. `exit` → `island-sealed`, and the bundle digest is stable across two
     exports.
  4. **No dual write**: during DEGRADED_LOCAL no request leaves the island
     for vuoro.cloud. A test transport asserts this; the island makes no
     cloud write calls at all, not even a freeze.
  5. An open epoch with a cloud probe healthy for more than 6 h → one
     `overdue` event. Writes are still accepted.
- sprintctl, `work.island.import-v1` against a seeded "cloud" database:
  1. One untouched item → `apply`.
  2. One item edited on the cloud side during the epoch → `conflict`. The
     cloud value is unchanged and the island change is preserved verbatim in
     the conflict record. This is the "never silently merged" falsifier.
  3. One island-created item → `create` with `island_id_map`. There is no
     integer-id collision, even when the cloud already has the same integer
     id.
  4. Replay → the same receipt.
  5. Different digest → `import-digest-mismatch`.
- Drill: extend the automated restore drill namespace
  (`vuoro-restore-drill`) with a scripted island epoch run end to end
  against a drill cloud instance. It runs on the drill schedule, and its
  receipt is attached as evidence. This is a check of done work, not a gate
  on entering the mode.

## 6. Migration

1. **Precondition: the Q1(b) cutover.** Until then the split-horizon §3.3
   Transition rule holds: each record family has one authoritative store,
   and vuoro-shared is authoritative for work items. The island mode code
   ships disabled. The `island_mode` table is empty, which reads as
   "pre-cutover", and NORMAL-mode refusals are not enforced. At the cutover
   the first `island_mode` row (`returned`, no epoch) is written, which
   turns NORMAL enforcement on.
2. **Work split by owner:**
   - **sprintctl:** epoch fields (§4.3), `work.island.import-v1`,
     `island.conflict`.
   - **vuoro:**
     - the mode record and refusals, the export bundle and the `island`
       CLI in vuoro-service
     - `island_endpoint` routing in vuoro-client
   - **vuoro-cloud:** the protected-origin import route, scope
     `vuoro:island.import`.
   - **appservice:** the `vuoro-ops` credential for import and the drill
     wiring.
3. **The cached read model** on vuoro-shared (needed for `base_revision`)
   comes from the reconciler's existing poll of vuoro.cloud. `base_watermark`
   makes its age explicit, so a stale cache yields more conflicts, never a
   silent overwrite.
4. **No existing record is rewritten.** Pre-cutover history keeps no epoch
   tag.
