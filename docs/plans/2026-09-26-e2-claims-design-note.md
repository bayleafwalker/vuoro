# E2 claims design note: what the durable lease owner must provide (2026-09-26)

**Status.** Deferred by E2 (agentops#2466), per the shared contract's own
sanctioned fallback (`docs/plans/2026-09-26-e2-e3-shared-contract.md` section
6, last line): `claim_tools.py` continues to return `None`, `vuoro:work.claim`
stays known-but-not-granted (already vuoro-cloud's state; nothing to change
there), and E2 ships run/evidence recording only.

## Why this was deferred rather than built

`vuoro_service/lease.py`'s `LeaseStore` is the exact behavioral contract a
durable claim implementation must satisfy: lease id (not holder) is what
counts as current; a superseded lease id is permanently dead; heartbeat and
completion against a dead lease fail even for the original holder; expiry is
checked before every mutation. That contract is small and precise, and this
session could have re-implemented it against Postgres with real confidence
(the same way `pg.py`'s new `run`/`evidence_item` tables were built and
verified against a real disposable database in this same session).

What made it a **risk, not just work**, is everything *around* that contract:

1. **Ownership.** Per `13-REPO-OWNERSHIP-AND-CHANGE-MATRIX.md` (vuoro-cloud),
   sprintctl owns work semantics. A claim is a mutation on a work item's
   coordination state, which is squarely inside sprintctl's existing
   `reservation` machinery (`sprintctl/pg.py`'s `reserve`/`reassign_reservation`/
   `release_reservation`, `sprintctl/reservation.py`), not a fresh, standalone
   lease table alongside it. Building E2's claim on a *new*, disconnected
   lease store (bypassing `reservation`) would create two competing sources of
   truth for "who is working on this item" — exactly the kind of half-correct
   result the shared contract asks this session to avoid ("Prefer this over a
   risky or half-correct claim implementation").
2. **Reconciling two coordination models under time pressure is exactly the
   kind of design decision that deserves its own review**, not one absorbed
   silently inside a session already carrying run/evidence/idempotency
   (agentops#2466's other half). `reservation`'s roles (`execution`,
   `verification`, `observation`, per `sprintctl/reservation_policy.py`) and
   states (`active`/`released`/`interrupted`) do not obviously map onto
   `claim_work`/`heartbeat`/`complete_work`'s three-verb, single-owner lease
   shape; overlap is reported, not refused, in `reservation.reserve` (see
   `sprintctl/pg.py`'s `reserve` docstring: "reporting -- never refusing --
   overlap"), whereas `LeaseStore.claim` and `.reclaim` *refuse* on a live
   lease. Whether `claim_work` should mean "reserve with `interrupt_existing`
   forced false and surface the conflict as a refusal" or something new is a
   product decision, not an implementation detail.
3. **The (handle, auth_context) binding.** The shared contract requires "the
   lease holder is the run's `RunBinding`, not just the token" (section 6).
   That means every claim operation needs a `run_id` resolved through
   `context.runs` *before* touching the lease, exactly like `append_evidence`
   and `write_session_note` do in this session's `record_tools.py`. That part
   is straightforward and this session already has a working pattern for it
   (`_require_owned_run` in `sprintctl/work_application.py`, and
   `SprintctlRecordStore.resolve` in `vuoro_mcp_edge/record_tools.py`) — it is
   not, by itself, the risk.

Given these, shipping a claim implementation in this session would have meant
inventing sprintctl's coordination-model decision under time pressure, which
is precisely the "risky or half-correct" outcome the contract tells E2 to
avoid in favor of this note.

## What the durable lease owner must provide

Whoever builds it next (a follow-up E2 continuation, or a dedicated item)
needs to supply:

1. **A decision on reservation vs. a new lease table**, made explicitly and
   reviewed, not inferred from code. If reservations are reused: `claim_work`
   maps to `reserve` with a role that refuses on conflict (a new role or a new
   `reserve` mode, since today's roles all coexist rather than mutually
   exclude), `heartbeat` maps to `note_session_activity`/`touch_reservation`
   with the `LeaseStore` expiry check added (reservations today have no TTL
   at all — `last_activity_at` is informational, not enforced), and
   `complete_work` maps to `release_reservation`. If a new table: it must
   still key off `work_item_id` so sprintctl's own reservation-conflict
   reporting is not blind to leases and vice versa, or explicitly document
   why the two are allowed to disagree.
2. **Lease id as the identity that matters**, not the holder alone (per
   `lease.py`): the new/adapted storage needs an explicit lease id column
   (reservations have `id`, which is *a* candidate, but it is never
   invalidated the way `LeaseStore.reclaim` invalidates a superseded lease id
   — a released reservation's id is simply inert, not distinguished from "was
   superseded" vs. "was never current"). Contract tests for this must be
   `lease.py`'s own contract tests (expiry-with-heartbeat, dead-superseded-id,
   holder-mismatch, replayed-completion-on-dead-lease), parametrized over
   both `LeaseStore` (the in-memory reference) and the new durable
   implementation — this session's `tests/pg/test_run_evidence.py` is the
   template for what a parametrized, real-Postgres contract test for this
   would look like.
3. **A `claim_work`/`heartbeat`/`complete_work` operation triple in
   sprintctl**, named and reviewed the same way `work.run.register-v1` etc.
   were in this session (`sprintctl/vuoro_adapter.py`'s
   `WORK_OPERATION_CONTRACTS`, `sprintctl/work_application.py`'s handler
   dispatch), each resolving the caller's `run_id` first via the same
   `context.runs.resolve` pattern before touching the lease, and each with its
   own idempotency handling (this session's `_idempotent_write` in
   `work_application.py` is directly reusable for this).
4. **The `claim_tools.py` toolset builder**, mirroring
   `record_tools.py`'s shape here: a `SprintctlLeaseStore`-equivalent class
   reaching the new operations through the same no-credential, forwarded-
   assertion invoke pattern (`RecordShellClient` in this session's
   `record_tools.py` is directly reusable, or a sibling of it).
5. **A decision on the two frozen-Protocol gaps this session found while
   building the run registry** (see the E2 final report): `RunRegistry`'s
   `register`/`resolve` have no parameter for the caller's forwarded
   assertion, which a claim implementation will hit identically the moment
   it needs `context.runs.resolve` for its own binding check. Resolving that
   in `runs.py` itself (a separate, reviewed PR against the shared contract,
   per that page's own rule) would remove the need for every future consumer
   to invent its own workaround.

## What is explicitly out of scope for this note

This note does not itself propose which of the two coordination models
(extend `reservation`, or a new lease table) is correct — that is exactly the
decision this session is declining to make under time pressure. It also does
not re-litigate whether `vuoro:work.claim` should be granted; that remains
vuoro-cloud's decision, unaffected by this session.
