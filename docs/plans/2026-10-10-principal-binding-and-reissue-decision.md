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

Served item: agentops#2482. Answers the open decision in
`docs/plans/2026-08-22-long-term-direction.md` §14 (":839 exact principal and
external-identity binding model") and falsifier 5 (§13, :790). This is a
decision record. It adds no ledger schema or code. The schema follows from
this decision as separate work (§6).

## 1. Problem

The long-term direction says (:499): "The logical agent identifier is not
itself an authorization principal. Each runtime actor receives an explicit
identity binding and `EffectGrant`. Reissuing a provider actor string must
never transfer historical ownership or authority."

Falsifier 5 (:790): **"Reissuing a provider actor string cannot acquire
historical ownership or authority."**

Today the served authority identifies a caller in two ways at once. One is a
display `actor` string (`workstation-vuoro`, `devbox-agent-vuoro`). The other
is a `principal_id`. Some records and checks use the principal; others use
only the string. Nothing says which one governs. E2 (agentops#2466, done)
validates `(handle, auth_context)` on every claim call and inherits whatever
this decision says.

## 2. Observed current behaviour (evidence)

Observed 2026-10-10 against the code on `origin/main` and the live served
endpoint.

**Handshake.** `sprintctl doctor` with the workstation profile prints
`identity: actor=workstation-vuoro`. It prints no principal or epoch. The
`work.identity.current` operation returns only `{repo_id, actor}`
(`sprintctl:sprintctl/work_application.py:866`). The principal that governs
ownership is therefore not visible to the client.

**The identity has two fields.** `Identity` carries `actor` (display) and
`principal_id = <issuer>:<subject>:<epoch>`. Its comment states the intent:
the epoch "increments on any reissue … so a reissued actor is a *different*
principal by construction and inherits nothing … there is no ownership
transfer operation" (`packages/vuoro-service/src/vuoro_service/identity.py:13-19,57-73`).

**Keyed on `principal_id` (holds falsifier 5):**

- runs: UNIQUE (repo, workspace, principal, idempotency_key)
  (`sprintctl/pg.py:2740-2758`)
- work leases: principal, workspace, client and grant
  (`pg.py:2910-2935`)
- the idempotency ledger (`pg.py:2799-2805`)
- effect intents: proposer, acceptor, rejector and applier principal
  (`pg.py:3066-3083`)
- the evidence intake binding (`evidence_intake.py:26,75`)

Claims reach all of these through `_claim_binding`/`_identity_binding`
(`work_application.py:33-45,2156-2164`). Another principal's run returns
`run-not-found`.

**Keyed on the `actor` string only (fails falsifier 5):**

- `event.actor` (`pg.py:164`) and `reservation.actor` (`pg.py:1535`). Neither
  table has a principal column.
- decisions (`db.py:2367`) and maintenance envelopes
  (`work_application.py:1280`).
- **Authority checks** that compare strings, with
  `arguments.actor == context.identity.actor`:
  - reservation reserve, reassign and release
    (`work_application.py:1724-1728,1840-1844,1849-1860`)
  - record-actor checks (`:3058`)
- **The work-resource observation grant authorizer**, which allows by
  `(identity.actor, repo_id)` (`vuoro_service/composition.py:555-564`).

**How the principal is minted:**

- **Gateway path** (vuoro.cloud).
  - `subject` is the opaque `users.id`, never the actor string.
  - `principal_id = f"{issuer}:{subject}:{epoch}"`.
  - An assertion without `principal_epoch` is refused
    (`gateway_identity.py:312-345,437-442`).
  - vuoro.cloud stores `principal_subjects(subject PK, actor, epoch)`. A
    trigger keeps the epoch monotonic and the subject immutable
    (`vuoro-cloud:migrations/008_principal_epoch.sql`).
  - The external identity is the GitHub numeric id, `actor=github:<id>`
    (`vuoro-cloud:src/vuoro_cloud/oauth.py:94`, `principal.py:21`), bound
    UNIQUE to `users.external_subject` (`001_control.sql`).
  - This path holds falsifier 5 by construction.
- **Static registry path** (vuoro-shared, `VUORO_IDENTITIES_FILE`,
  `vuoro-identities/v1`).
  - The registry requires `principal_id`
    (`composition.py:607-612`: "an actor rename would move every resource it
    owns to whoever holds the name next").
  - The author writes the value by hand. Nothing mints it, nothing enforces
    the epoch, and nothing records that a principal was retired. No revoke or
    reissue record exists anywhere in sprintctl or vuoro-service.

**What a reissued actor name inherits today (inferred from the code above):**

| Reissue form | Runs, leases, idempotency, intents, evidence | Reservations, record-actor checks, maintenance identity, observation grants, event/decision attribution |
|---|---|---|
| New token, same actor, **new** principal_id | nothing | **everything** (string match) |
| New token, same actor, **copied** principal_id (static registry) | **everything** | **everything** |

**Verdict on current behaviour: fails falsifier 5.** It fails on the
actor-string authority checks for both reissue forms. It also fails on the
static registry, which can rebind an old principal_id to a new credential.

## 3. Decision

**Binding model.**

1. **The principal is the only ownership and authorization key.** A principal
   is `principal_id = <issuer>:<subject>:<epoch>`.
   - `issuer`: the credential issuer (vuoro.cloud control service, or the
     static registry's named issuer).
   - `subject`: an opaque, issuer-minted, never-reused identifier. It is
     never derived from the display string.
   - `epoch`: a monotonic integer per `(issuer, subject)`.
   - Every grant, lease, reservation, run, intent, and every authority check
     that today compares actor strings, binds to `principal_id`.
2. **The actor string is attribution only.** `actor` is a display label. It
   is recorded next to `principal_id` and never compared for authority. Two
   principals may carry the same actor string, at the same time or one after
   another.
3. **External identity binds once to a subject.**
   - vuoro.cloud: an external identity (GitHub numeric id) binds UNIQUE to
     one `users.id` subject. A rename of the GitHub login changes nothing.
   - Static registry: the issuer mints the subject.
   - In both cases, rebinding an external identity to a different subject is
     a new subject, not a transfer.
4. **Reissue is an explicit revoke-then-new-epoch record.**
   - To reissue a credential for the same subject, the issuer records
     `principal.retired {principal_id, retired_at, reason}` and issues the
     next epoch.
   - The new `principal_id` owns nothing that the retired one owned.
   - There is no ownership-transfer operation. Work that must continue moves
     by the existing explicit operations, each recorded and each naming both
     principals:
     - lease takeover events name `previous_principal` / `new_principal`
       (`docs/plans/2026-09-26-e2-e3-shared-contract.md:265`)
     - release to pending followed by a fresh claim
5. **A retired principal_id can never be bound again.** Loading a static
   registry that maps a credential to a principal_id that is retired, or that
   is recorded against a different credential fingerprint, is refused.

**Falsifier 5, answered: YES, it holds as decided.** A reissued actor string
cannot acquire historical ownership or authority. The mechanism has two
parts:

- a non-reusable `principal_id`, distinct from the display string, is the
  only authority key
- reissue is an explicit `principal.retired` record plus a new epoch, and the
  loader refuses to rebind a retired principal

As observed in §2, the current code does **not** yet satisfy this. The
corrective change is in §4.

**Consistency with #2470 / E1.**

- This does not reopen E1 scopes. #2470's static-bearer acceptance was
  superseded on 2026-09-22 by OAuth 2.1 (agentops#2514;
  `docs/plans/2026-09-20-vuoro-at-the-edge.md:303-305`).
- The scope set is unchanged:
  - `vuoro:work.read` and `vuoro:work.claim`
  - grant instances bound to `lease_id`
  - deliberately no `vuoro:effect.apply`
- Because a lease is principal-keyed, a grant bound to `lease_id` is
  principal-keyed by transitivity. That is consistent with this decision.
- No credential path to a runtime the operator does not host is added
  (TS-16).

## 4. Corrective change (required)

| # | Change | Owner repo | Must land before |
|---|---|---|---|
| C1 | Add `principal_id` to `reservation` and `event` (new rows; old rows stay with `principal_id = null` and are attributed only, never authorizing). Reserve, reassign, release and record-actor checks compare `principal_id`. | sprintctl | Any second credential bearing an existing actor string is issued. |
| C2 | The observation grant authorizer keys on `(principal_id, repo_id)`. | vuoro (vuoro-service) | Same as C1. |
| C3 | Add a static registry principal ledger: an append-only `principal_binding {principal_id, actor, credential_fingerprint (sha256 of the token), bound_at, retired_at}`. The loader refuses `principal-rebind-refused` for a retired principal, or for a different fingerprint on a live one. A rotation retires the old row and requires the next epoch. | vuoro (vuoro-service) | Same as C1. |
| C4 | `work.identity.current` and `sprintctl doctor` print `principal_id`, so the evidence in §2 is re-derivable from the handshake. | sprintctl + vuoro-service | With C1. |

**Relation to #2466 (E2).** E2 shipped and its claim path is already
principal-keyed, so it does not need to be reverted. Two places still
compare actor strings: the reservation checks and the observation grant.
C1-C3 must land:

- before the `workstation-mi1-native` commissioning issues another
  credential (`docs/plans/2026-10-06-native-record-identity.md:36-41`
  already forbids reusing the old principal; C3 turns that rule into a
  check)
- before the Q1(b) cutover moves local harnesses to vuoro.cloud identities

## 5. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Keep the actor string as principal and forbid reuse by convention | Not checkable. Today's registry already depends on authors not copying ids, and nothing catches a mistake. |
| Allow an explicit ownership-transfer operation on reissue | It recreates exactly the inheritance that falsifier 5 forbids. Existing takeover and release operations already move work with both principals named. |
| Derive `subject` from actor or environment names | A rename or reissue would then collide with history (`composition.py:607-612`). |
| Require operator approval for each reissue | An approval gate adds nothing to safety: the loader check and the retired ledger make a wrong rebind impossible, not merely reviewed. |
| Rewrite historical `event.actor` rows to principals | History is append-only. Old rows stay attribution-only, and authority never reads them. |

## 6. Contract

**`principal_id` grammar:** `^[a-z0-9.-]+:[A-Za-z0-9_-]+:[1-9][0-9]*$` (issuer,
colon-free opaque subject, epoch at least 1).

**Records:**

| Record | Fields | Notes |
|---|---|---|
| `principal.bound` | `principal_id`, `actor`, `credential_fingerprint`, `bound_at`, `issuer` | One row per credential. Append-only. |
| `principal.retired` | `principal_id`, `retired_at`, `reason` | Terminal. The principal is never bound again. |

**Errors:**

| Code | HTTP | When |
|---|---|---|
| `principal-rebind-refused` | load-time refusal (service does not start) | The registry binds a retired principal_id, or a live principal_id with a different credential fingerprint. |
| `principal-epoch-regressed` | load-time refusal | The epoch for `(issuer, subject)` is lower than the recorded maximum. |
| `reservation-not-owned` | 403 | Reserve, reassign or release by a principal other than the holder's, whatever the actor string. |
| `run-not-found` | 404 | Unchanged: another principal's run. |

## 7. Negative observation and test sketch

**Observation (today, read-only).** Re-run the following and compare the
output with §2:

```bash
SPRINTCTL_BACKEND=served SPRINTCTL_VUORO_PROFILE=<workstation profile> sprintctl doctor
```

It prints `identity: actor=workstation-vuoro` and no principal. Grep
`arguments.actor == context.identity.actor` in
`sprintctl/work_application.py`; it is present at the lines cited.

**Test T-reissue (a falsifier; must fail on today's code and pass after
C1-C3).** This runs in `packages/vuoro-service/tests` against a disposable
Postgres with sprintctl pinned.

1. Registry R1: token A has actor `workstation-vuoro` and principal
   `static:ws01:1`. With A:
   - reserve item X
   - register run r1
   - claim item Y (lease L1)
   - append one evidence entry
2. Registry R2 is a proper reissue: it retires `static:ws01:1`, and token B
   has the **same actor** `workstation-vuoro` and principal `static:ws01:2`.
   Restart the service on R2.
3. With B, assert the following. Each must be refused, and each refusal must
   produce no event:
   - release or reassign the reservation on X: `reservation-not-owned`
     (**fails today**: the string match succeeds)
   - resume r1: `run-not-found`
   - heartbeat or report on L1: lease or run refusal
   - read through an observation grant issued to A: refused
     (**fails today**: the authorizer is actor-keyed)
4. Registry R3 is a bad reissue: token B with the **copied** principal
   `static:ws01:1`. Service start refuses with `principal-rebind-refused`
   (**fails today**: R3 loads and B inherits everything).
5. Attribution is preserved: past events still show `actor=workstation-vuoro`
   with `principal_id=null` for pre-C1 rows, or `static:ws01:1` for post-C1
   rows. No B write carries `static:ws01:1`.

Pass criteria: steps 3-5 all hold. Steps 3 (reservation and grant) and 4 are
the negative observations that today's code produces. Recording those
failing results on this item before the fix lands is the evidence that the
test discriminates.

## 8. Verification plan

- T-reissue (§7) in the vuoro-service suite, plus sprintctl unit tests for
  principal-keyed reservation checks (C1).
- After C4: `sprintctl doctor` shows `principal_id`. Re-running the §7
  observation shows the principal, and a second profile with the same actor
  string shows a different one.
- Cheap static check on done work: a test greps that no authority path
  compares `identity.actor` (an allowlist of attribution-only uses).

## 9. Migration

1. C1 is additive: a nullable `principal_id` on `reservation` and `event`.
   New writes fill it, and old rows are never back-filled with authority. A
   reservation held at migration time is re-bound once, by recording the
   principal of its current live holder from the registry. This applies only
   where exactly one live principal carries that actor string. Otherwise the
   reservation is released to pending with a recorded reason.
2. C3 seeds `principal_binding` from the current registry at first start (one
   `principal.bound` row per entry). Every later change goes through it.
3. The vuoro.cloud gateway path already conforms. It needs only C2 when the
   edge serves observation grants there.
4. Schema follows this decision. No ledger schema is changed by this
   document.
