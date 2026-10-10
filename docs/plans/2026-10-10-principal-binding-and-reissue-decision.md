---
doc_id: vuoro-principal-binding-reissue-decision
title: "Decision: principal and external-identity binding; reissued actor strings acquire nothing"
purpose: decision
lifecycle: proposed
effective: 2026-10-10
applies_to:
  components: [vuoro-core, sprintctl]
related: [vuoro-long-term-direction]
---

# Decision: principal and external-identity binding, and actor-string reissue

Served item: agentops#2482. This record answers the open decision in
`docs/plans/2026-08-22-long-term-direction.md` §14 (:844, "exact principal and
external-identity binding model") and falsifier 5 (§13, :791). It is a
decision record. It adds no ledger schema or code; the schema follows from
this decision as separate work (§4).

## 1. Problem

The long-term direction says (:499): "The logical agent identifier is not
itself an authorization principal. Each runtime actor receives an explicit
identity binding and `EffectGrant`. Reissuing a provider actor string must
never transfer historical ownership or authority."

Falsifier 5 (:791): **"Reissuing a provider actor string cannot acquire
historical ownership or authority."**

The served authority identifies a caller in two ways at once:

- a display `actor` string (`workstation-vuoro`, `devbox-agent-vuoro`)
- a `principal_id`

Some records and checks use the principal; others use only the string.
Nothing says which one governs. E2 (agentops#2466, done) validates
`(handle, auth_context)` on every claim call, and it inherits whatever this
decision says.

## 2. Observed current behaviour (evidence)

These observations were made on 2026-10-10 against `origin/main` (sprintctl
126028e) and the live served endpoint.

**Handshake.** `sprintctl doctor` with the workstation profile prints
`identity: actor=workstation-vuoro` and nothing about a principal or epoch.
`work.identity.current` returns only `{repo_id, actor}`
(`sprintctl:sprintctl/work_application.py:866`). The principal that governs
ownership is therefore invisible to the client.

**The identity has two fields.** `Identity` carries `actor` (display) and
`principal_id = <issuer>:<subject>:<epoch>`. Its comment states the intent:
the epoch "increments on any reissue … so a reissued actor is a *different*
principal by construction and inherits nothing … there is no ownership
transfer operation" (`packages/vuoro-service/src/vuoro_service/identity.py:13-19,57-73`).

**Ownership keyed on `principal_id` (holds falsifier 5):**

- runs: UNIQUE (repo, workspace, principal, idempotency_key)
  (`sprintctl/pg.py:2740-2758`)
- work leases: principal, workspace, client and grant (`pg.py:2910-2935`)
- the idempotency ledger (`pg.py:2799-2805`)
- effect intents: proposer, acceptor, rejector and applier principal
  (`pg.py:3066-3083`)
- the evidence intake binding (`evidence_intake.py:26,75`)

Claims reach these through `_claim_binding` and `_identity_binding`
(`work_application.py:33-45,2156-2164`). Another principal's run returns
`run-not-found`.

**Reservations are advisory and confer no ownership (not a falsifier-5
surface).**

- The checks at `work_application.py:1724-1728,1840-1844,1849-1860` are
  *self-attribution* checks. The `actor` argument a caller supplies must
  equal its authenticated `identity.actor`.
- The owner has no holder check:
  - `reassign_reservation` and `release_reservation` act on any active
    reservation for any caller (`pg.py:4819-4847`)
  - reassign records `previous_actor` as a deliberate takeover
  - `touch_reservation` checks only `session_id` (`pg.py:4812`)

That is coordination without custody, and this decision keeps it. Holding a
reservation grants nothing, so a reissued principal cannot inherit anything
through one. The defect here is attribution only. `reservation.actor` and
`event.actor` (`pg.py:164,1535`) record only the string, so rows written by
a reissued principal cannot be told apart from rows its predecessor wrote.

**Authority keyed on the `actor` string (fails falsifier 5):**

- The work-resource observation grant authorizer allows by
  `(identity.actor, repo_id)` (`vuoro_service/composition.py:555-564`). An
  observation grant issued while actor `workstation-vuoro` was principal P1
  is usable by any later principal that carries the same string.
- The record-actor check (`work_application.py:3058`) and the maintenance
  envelope identity (`:1280`) compare strings. They are self-attribution
  checks, and they identify "the same caller" only by string.

**Principal minting:**

- **Gateway path (vuoro.cloud): holds falsifier 5 by construction.**
  - `subject` is the opaque `users.id`, never the actor string.
  - `principal_id = f"{issuer}:{subject}:{epoch}"`.
  - An assertion without `principal_epoch` is refused
    (`gateway_identity.py:312-345,437-442`).
  - vuoro.cloud stores `principal_subjects(subject PK, actor, epoch)`. The
    epoch starts at 0 and a trigger keeps it monotonic; the subject is
    immutable (`vuoro-cloud:migrations/008_principal_epoch.sql`).
  - The external identity is the GitHub numeric id, recorded as
    `actor=github:<id>` (`vuoro-cloud:src/vuoro_cloud/oauth.py:94`,
    `principal.py:21`). It is bound UNIQUE to `users.external_subject`
    (`001_control.sql`).
- **Static registry path (vuoro-shared, `VUORO_IDENTITIES_FILE`,
  `vuoro-identities/v1`): fails falsifier 5.**
  - The registry requires `principal_id` (`composition.py:607-612`: "an
    actor rename would move every resource it owns to whoever holds the name
    next").
  - The author writes the value by hand. Nothing mints it, nothing enforces
    the epoch, and nothing records retirement.
  - No revoke or reissue record exists in sprintctl or vuoro-service. A new
    token mapped to a copied `principal_id` inherits every principal-keyed
    run, lease, idempotency entry and intent.

**What a reissued actor name inherits today:**

| Reissue form | Runs, leases, idempotency, intents, evidence | Observation grants | Reservations | Event and reservation attribution |
|---|---|---|---|---|
| New token, same actor, **new** principal_id | nothing | **everything** (actor-keyed) | nothing to inherit (advisory) | indistinguishable from the predecessor's rows |
| New token, same actor, **copied** principal_id | **everything** | **everything** | nothing to inherit | indistinguishable |

**Verdict on current behaviour: fails falsifier 5.** It fails on two
surfaces: the observation grant authorizer, and the static registry's
ability to rebind a copied principal_id. Attribution rows are also
ambiguous. That ambiguity is not an authority failure, but it does stop the
historical record from separating the predecessor from its successor.

## 3. Decision

**Binding model.**

1. **The principal is the only ownership and authorization key.**
   `principal_id = <issuer>:<subject>:<epoch>`, where:
   - `issuer` is the credential issuer: the environment id, e.g.
     `vuoro-cloud`, or the static registry's issuer
   - `subject` is opaque, issuer-minted, never reused, and never derived
     from the display string
   - `epoch` is a monotonic integer per `(issuer, subject)`, starting at 0

   Every grant, lease, run, intent and observation grant binds to
   `principal_id`.
2. **The actor string is attribution only.** `actor` is a display label. It
   is recorded alongside `principal_id` and is never compared for
   authority. Two principals may carry the same actor string, at the same
   time or in sequence.
3. **Reservations stay advisory.** No holder enforcement is added. Anyone
   may reserve, reassign or release, as today. Each reservation row and
   event records the acting `principal_id` so that history separates
   principals, and reassignment records `previous_principal`.
4. **Self-attribution binds to the principal.**
   - Where a caller supplies `actor` today, the check stays: the argument
     must equal `identity.actor`. The written row also records
     `identity.principal_id`, which the caller cannot supply.
   - Where a check asks whether the caller is the author of an existing
     record (record-actor :3058, maintenance envelope :1280), it compares
     `principal_id`, not the string.
5. **External identity binds once to a subject.**
   - vuoro.cloud binds the GitHub numeric id UNIQUE to one `users.id`
     subject. A login rename changes nothing.
   - The static registry issuer mints its subjects.
   - Rebinding an external identity to a different subject creates a new
     subject. It is never a transfer.
6. **Reissue is an explicit revoke-then-new-epoch record.**
   - The issuer records `principal.retired {principal_id, retired_at,
     reason}` and issues the next epoch.
   - The new `principal_id` owns nothing that the retired one owned.
   - There is no ownership-transfer operation. Work moves only through the
     existing explicit operations, each recorded and naming both
     principals: a lease takeover with `previous_principal` and
     `new_principal` (`docs/plans/2026-09-26-e2-e3-shared-contract.md:265`),
     or a release to pending followed by a fresh claim.
7. **A retired principal_id is never bound again.** A static registry that
   maps a credential to a retired principal_id, or to a live principal_id
   recorded against a different credential fingerprint, is refused at load.

**Falsifier 5, answered: YES, it holds as decided.** A reissued actor string
cannot acquire historical ownership or authority. Three mechanisms
guarantee it:

- a non-reusable `principal_id`, distinct from the display string, is the
  only authority key, and that includes observation grants
- reissue is an explicit `principal.retired` record plus a new epoch
- the loader refuses to rebind a retired or differently fingerprinted
  principal

The current code does **not** yet satisfy this (§2). The corrective change
is in §4.

**Consistency with #2470 and E1.** This decision does not reopen the E1
scopes:

- #2470's static-bearer acceptance was superseded on 2026-09-22 by OAuth 2.1
  (agentops#2514; `docs/plans/2026-09-20-vuoro-at-the-edge.md:303-305`).
- The scope set is unchanged:
  - `vuoro:work.read` and `vuoro:work.claim`
  - grant instances bound to `lease_id`
  - deliberately no `vuoro:effect.apply`
- A lease is principal-keyed, so a grant bound to `lease_id` is
  principal-keyed by transitivity.
- No credential path to a runtime the operator does not host is added
  (TS-16).

## 4. Corrective change (required)

| # | Change | Owner repo | Must land before |
|---|---|---|---|
| C1 | Add a nullable `principal_id` to `reservation` and `event`, and `previous_principal` to the reassignment payload. New rows record `identity.principal_id`. The record-actor and maintenance-envelope author checks compare `principal_id`. No reservation holder enforcement is added. | sprintctl | Any second credential bearing an existing actor string is issued. |
| C2 | The observation grant authorizer keys on `(principal_id, repo_id)`. | vuoro (vuoro-service) | Same as C1. |
| C3 | A static registry principal ledger: append-only `principal_binding {principal_id, actor, credential_fingerprint (sha256 of the token), bound_at, retired_at}`. The loader refuses `principal-rebind-refused` and `principal-epoch-regressed`. A rotation retires the old row and requires the next epoch. | vuoro (vuoro-service) | Same as C1. |
| C4 | `work.identity.current` and `sprintctl doctor` print `principal_id`, so the evidence in §2 is re-derivable from the handshake. | sprintctl + vuoro-service | With C1. |

**Relation to #2466 (E2).** E2 shipped, and its claim path is already
principal-keyed, so nothing there needs reverting. C2 and C3 close the two
authority gaps, and C1 and C4 close the attribution gap. C1-C3 must land:

- before the `workstation-mi1-native` commissioning issues another
  credential. `docs/plans/2026-10-06-native-record-identity.md:36-41`
  already forbids reusing the old principal; C3 turns that rule into a
  check.
- before the Q1(b) cutover moves local harnesses to vuoro.cloud identities.

## 5. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Keep the actor string as principal and forbid reuse by convention | Not checkable: today's registry already depends on authors not copying ids. |
| Add reservation holder enforcement (`reservation-not-owned`) | Reservations are advisory coordination, not custody. Reassign is a deliberate, recorded takeover. Holder enforcement would add custody semantics that falsifier 5 does not need, because a reservation confers nothing to inherit. |
| An explicit ownership-transfer operation on reissue | Recreates exactly the inheritance that falsifier 5 forbids. Takeover and release already move work, with both principals named. |
| Derive `subject` from the actor or environment name | A rename or reissue would collide with history (`composition.py:607-612`). |
| Operator approval of each reissue | An approval gate that adds nothing: the loader check and the retired ledger make a wrong rebind impossible, not merely reviewed. |
| Rewrite historical `event.actor` rows with principals | History is append-only. Pre-C1 rows stay attribution-only with `principal_id = null`. |

## 6. Contract

**`principal_id` grammar.** The grammar admits every principal minted
today:

```text
^[A-Za-z0-9._-]+:[A-Za-z0-9._-]+:(0|[1-9][0-9]*)$
```

- **issuer:** colon-free. Issuers in use include `vuoro-cloud`, which must
  equal the gateway's environment id
  (`vuoro-cloud:examples/tenant-runtime-values.yaml:37`), and
  `vuoro-static` (`packages/vuoro-service/tests/test_composition.py:778`). The gateway validates
  the issuer only as non-empty and stripped. C3's loader enforces this
  grammar.
- **subject:** matches `_PRINCIPAL_SUBJECT` (`gateway_identity.py:38`).
- **epoch:** an integer of 0 or more. This matches `008_principal_epoch.sql`
  (`DEFAULT 0 CHECK (epoch >= 0)`) and `gateway_identity.py:331`.

**Records:**

| Record | Fields | Notes |
|---|---|---|
| `principal.bound` | `principal_id`, `actor`, `credential_fingerprint`, `bound_at`, `issuer` | One row per credential. Append-only. |
| `principal.retired` | `principal_id`, `retired_at`, `reason` | Terminal. The principal is never bound again. |

**Errors:**

| Code | Surface | When |
|---|---|---|
| `principal-rebind-refused` | load-time refusal (service does not start) | The registry binds a retired principal_id, or a live principal_id with a different credential fingerprint. |
| `principal-epoch-regressed` | load-time refusal | The epoch for `(issuer, subject)` is lower than the recorded maximum. |
| `observation-grant-refused` | 403 (existing authorizer refusal) | The caller's `principal_id` is not the one the grant names, whatever the actor string. |
| `run-not-found` | 404 (unchanged) | Another principal's run. |

A retired principal's token is absent from the registry. It is refused as
unauthenticated by the existing exact-token lookup
(`identity.py:111-122`), so no new error is needed.

## 7. Negative observation and test sketch

**Observation (today, read-only).** Run:

```bash
SPRINTCTL_BACKEND=served SPRINTCTL_VUORO_PROFILE=<workstation profile> sprintctl doctor
```

It prints `identity: actor=workstation-vuoro` and no principal. Then read
`vuoro_service/composition.py:555-564`. The observation grant authorizer
allows by `(identity.actor, repo_id)`.

**Test T-reissue.** This falsifier must fail on today's code and pass after
C1-C4. It runs in `packages/vuoro-service/tests` against a disposable
Postgres with sprintctl pinned. Each step tests something the decision
distinguishes and today's code does not.

1. Registry R1: token A has actor `workstation-vuoro` and principal
   `vuoro-static:ws01:0`. With A:
   - issue an observation grant G for repo `sprintctl`
   - register run r1
   - claim item Y, which creates lease L1
   - reserve item X
2. Registry R2 is a proper reissue: retire `vuoro-static:ws01:0`, and give
   token B the **same actor** `workstation-vuoro` and principal
   `vuoro-static:ws01:1`. Restart on R2.
3. **Observation grant.** B reads through G and must be refused
   (`observation-grant-refused`). **Today this fails**: the authorizer is
   actor-keyed and B passes.
4. **Retired token.** A's token must be refused as unauthenticated.
   Regression check: this passes today when R2 omits A.
5. **Bad reissue.** Registry R3 maps token B to the **copied** principal
   `vuoro-static:ws01:0`. Service start must refuse with
   `principal-rebind-refused`. **Today this fails**: R3 loads, and B
   inherits r1, L1 and G.
6. **Self-attribution separates principals.** B releases or reassigns X.
   This must succeed, because reservations are advisory and no holder check
   is added. The resulting `reservation.released` or
   `reservation.reassigned` event records `principal_id =
   vuoro-static:ws01:1`, and a reassignment records
   `previous_principal = vuoro-static:ws01:0`. **Today this fails**:
   neither row has a principal, so B's event is indistinguishable from
   one A wrote.
7. **Author check.** A record-actor check at `:3058` against a record A
   authored, made by B with the same actor string, must treat B as
   a different author. **Today this fails**: the string matches.
8. **Ownership regression.** B resuming r1 gets `run-not-found`, and B
   heartbeating L1 gets a lease refusal. Both pass today; this step guards
   them.

Pass criteria: steps 3-8 all hold. Steps 3, 5, 6 and 7 are the negative
observations that today's code produces. Recording those failing results on
this item before the fix lands is the evidence that the test discriminates.

## 8. Verification plan

- T-reissue (§7) in the vuoro-service suite, plus sprintctl unit tests for
  C1's principal recording and author comparison.
- After C4, `sprintctl doctor` shows `principal_id`. A second profile with
  the same actor string shows a different one.
- A cheap static check on done work: a test greps that no authority path
  compares `identity.actor`. The allowlist covers only the self-attribution
  argument checks and display uses.

## 9. Migration

1. C1 is additive. It adds nullable columns. New writes fill them, and old
   rows are not back-filled. Reservations need no re-binding, because they
   confer nothing.
2. C3 seeds `principal_binding` from the current registry at first start,
   with one `principal.bound` row per entry and the current epoch, which may
   be 0. Every later change goes through it.
3. The vuoro.cloud gateway path already mints conforming principals
   (`<environment_id>:<users.id>:<epoch>`, with epoch ≥ 0). It needs only
   C2 when the edge serves observation grants there.
4. The schema follows this decision. This document changes no ledger
   schema.
