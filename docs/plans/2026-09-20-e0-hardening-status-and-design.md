# E0 hardening — status and design for the remaining controls (agentops#2464)

**Update (second pass):** acceptances (b), (c), (d) and (e) are now
implemented and tested, and the evidence-chaining gap this note flagged
(nothing called `link`/`verify_chain`) is closed at the interim scope this
note recommended. See "Landed in the second pass" below. The original design
notes for (b)-(e) are left as-is beneath it for the record of what was
planned before implementation, plus what changed against the plan.

## Landed in the second pass

- **(e) token audience validation** — `gateway_identity.py` already checked
  `aud` via PyJWT's `audience=` comparison (pre-dating this item); the gap
  was that "aud" wasn't in `_REQUIRED_CLAIMS`, so a token with *no* audience
  claim at all relied on PyJWT's implicit default rather than an explicit,
  documented requirement. Added `"aud"` to `_REQUIRED_CLAIMS` and a test
  (`test_gateway_assertion_resolver_rejects_assertion_with_no_audience_claim_at_all`)
  that fails if either the explicit requirement or the value comparison is
  removed (checked both ways).
- **(b) lease TTL with heartbeat** — new `vuoro_service/lease.py`,
  implementing the design below almost exactly: `Lease`, `LeaseStore.claim`/
  `reclaim`/`heartbeat`/`complete`, all keyed by `subject` with one live
  lease at a time, using an injectable clock. A completion or heartbeat
  against a lease id that has been superseded by a reclaim fails with
  `LeaseNotCurrentError` regardless of whether the caller still has the
  right holder name -- `lease_id` currency, not holder identity, is what
  "current" means, which is what makes a stale holder's replayed completion
  fail. Not wired into an HTTP endpoint yet -- there is still no `claim_work`
  consumer in vuoro's service layer -- so this lands as a tested primitive
  the way `chain.py` did in the first pass; the recorded claim-evidence
  question ("needs a decision" below) is still open.
- **(c) rate limiting** — new `vuoro_service/rate_limit.py`
  (`RateLimiter`, token bucket per `(token, ip)` key) wired into
  `app.py`'s `_dispatch`, evaluated before identity resolution so an
  abusive caller is rejected before paying for crypto/identity work. `429`
  with `error_code: "rate-limit-exceeded"`, verified distinct in status and
  code from the `401 identity-required` case in the same test file. `None`
  by default (opt-in via `create_app(rate_limiter=...)`), matching the
  `readiness_check` pattern already used for optional runtime wiring.
- **(d) endpoint monitoring** — new `vuoro_service/metrics.py`
  (`RequestMetrics`: request/error counts, bounded rolling latency sample,
  p50/p95), wired around the same `_dispatch` path and exposed at
  `GET /health/metrics` (JSON, always mounted -- next to the pre-existing
  `/health/live` and `/health/ready`, not a new probe surface).
- **Evidence-chain wiring** — new `vuoro_evidence/core/set_builder.py`:
  `EvidenceSetBuilder` is the writer (every `add()` runs the item through
  `chain.link` before storing it, so `chain_seq`/`chain_prev_digest` can
  never be forgotten or misassigned) and `verify_evidence_set` is the read
  path (re-derives chain-break diagnostics from a loaded `EvidenceSet`).
  Scoped by `EvidenceSet.set_id`, exactly the interim this note recommended
  below, **not** wired into `ingress/hostproto.py` or `vuoro-service`
  composition -- that wiring still needs the run-scope decision this note
  raised, which depends on the rebuild's run-tracking object (agentops#2479,
  not landed). When that lands, `EvidenceSetBuilder`'s `extend`/`build`
  shape is meant to be reused directly against the new run-scoped key.

## Original notes (before the second pass; kept for the record)

Landed in the first pass: **evidence chaining** only (acceptance (a)). This
note recorded the design for (b)-(e) so the next pass did not re-derive it.

## Landed: evidence chaining

`packages/vuoro-evidence/src/vuoro_evidence/core/chain.py`:
- `EvidenceItem` gained two optional fields, `chain_seq: int | None` and
  `chain_prev_digest: str | None`, default `None` (no ingress caller
  constructs `EvidenceItem` outside `vuoro-evidence` today, so this is
  additive and backward compatible — checked by grep across the tree).
- `entry_digest(item)` — the JCS-canonical sha256 over
  `{item_id, digest, chain_seq, chain_prev_digest}`; this is what the next
  item's `chain_prev_digest` must equal.
- `link(items, next_item)` — a builder that extends a chain in order.
- `verify_chain(items, expected_start_prev=None)` — detects: an unchained
  item, out-of-order `chain_seq`, a missing predecessor past the root, and
  an altered/forged predecessor (covers dropped, reordered and tampered
  entries — a drop shows up as the *next* surviving entry's predecessor no
  longer matching, since the digest a dropped entry contributed is gone).
  `expected_start_prev` lets a caller verify only a new run's entries
  against a persisted chain's last digest, without re-hashing history.
- Tests: `packages/vuoro-evidence/tests/test_chain.py`, 11 cases including
  altered/missing/dropped/reordered predecessor and continuation-verify.

Not done: nothing in `ingress/hostproto.py` or the service layer actually
*calls* `link`/`verify_chain` yet — no writer chains its own output, and no
persistence layer verifies a chain on read. The primitive is real and
tested; wiring a writer to use it needs a decision about the chain's scope
(per-run? per-EvidenceSet? see below) that touches `vuoro-service`
composition, which is a larger, riskier change to make without a design
review first.

**Open scoping decision:** the item's Acceptance (a) says "the run's
chain" — this implementation chains whatever sequence of items is passed to
`link`/`verify_chain`, and leaves scope (per-run vs. per-session vs.
global) to the caller. Recommend per-run (keyed by the RunManifest the
rebuild's Phase 1 introduces — not yet landed in vuoro — or by
`EvidenceSet.set_id` until then), so a compromised writer for run A cannot
extend or rewrite run B's chain.

## Design: lease expiry with heartbeat (ADR-02) — acceptance (b)

No `claim_work`/lease code exists anywhere in vuoro today (checked:
`grep -rn "claim_work\|class Lease\|TTL\|ttl" packages` is empty outside
this note). This is new surface, not a hardening of something present, and
per the edge doc it depends on rebuild Phase 2 (leases) and Phase 1
(RunManifest), neither of which has landed in vuoro yet either. Building it
now means inventing the storage and API shape with no existing lease
consumer to design against — real risk of designing the wrong shape twice.

Recommended shape (for review, not adopted):
- A `Lease` record: `lease_id`, `subject` (what's claimed), `holder`,
  `issued_at`, `ttl_seconds`, `last_heartbeat_at`.
- `is_expired(lease, now) = now > last_heartbeat_at + ttl_seconds`.
- `heartbeat(lease_id, holder)` — rejects if `holder` doesn't match or the
  lease already expired and was reclaimed (idempotency: a heartbeat racing
  a reclaim must lose, never resurrect a lease someone else now holds).
- `reclaim(subject)` — only valid when the current lease `is_expired`;
  returns a new lease for the new holder and should itself be recorded as
  evidence (an `EFFECT_UNCERTAIN`-adjacent claim type, or a new claim type —
  needs a decision, since the existing `ClaimType` vocabulary in
  `core/model.py` has nothing for "lease reclaimed").
- Acceptance test shape: construct a lease with a short TTL, advance a
  fake clock (not real sleep) past `ttl_seconds` with no heartbeat, assert
  `is_expired`, assert `reclaim` succeeds for a different holder and a
  stale heartbeat against the old lease is rejected.

## Design: rate limiting on the surface — acceptance (c)

Depends on `packages/vuoro-service` (the HTTP shell) having an endpoint to
guard; today no MCP surface is exposed (correctly — E1-E4 are blocked on
this item). Recommend a token-bucket per `(token, IP)` pair, evaluated in
middleware before the handler runs, returning HTTP 429 with a body
distinguishable from a 401/403 auth failure (the acceptance line is
explicit about this — reuse the existing error-shape module in
`vuoro_service/contracts.py` rather than a bespoke shape). Configurable
limits belong in the same config surface as `gateway_identity.py`'s token
config, not a new one.

## Design: endpoint monitoring — acceptance (d)

"An endpoint health/monitoring signal exists and is queryable" — cheapest
correct version is a `/healthz`-style endpoint plus counters (request
count, error count, p50/p95 latency) exposed at a queryable path, wired
into whatever `vuoro-service` already uses for structured logging
(`serve_logging` exists per the test file list — worth reusing its
transport rather than adding a second telemetry path).

## Design: token audience validation — acceptance (e)

`gateway_identity.py` exists and is where token handling already lives
(grep hit above). The change is: validate the token's `aud`/`iss` claim
against an expected value at verification time, not merely check presence.
This is the smallest of the five and the best candidate for the *next*
safe subset — it touches one existing file, needs no new storage, and has
a narrow, easily-testable acceptance ("wrong audience, otherwise
well-formed, is rejected"). Recommend doing this one next, before lease/
rate-limit/monitoring, since it's isolated and low-risk.

## Why the rest was not attempted in this pass

Three of the four remaining controls (rate limiting, monitoring, audience
validation) require reading and safely extending `vuoro-service`'s HTTP
shell and identity code, which are larger, less-inspected files than
`vuoro-evidence`'s small, already-boundary-tested core; the fourth (leases)
has no existing code to extend at all and depends on RunManifest, which
hasn't landed. Given the effort budget for this pass, landing evidence
chaining correctly and fully tested, plus a concrete, reviewable design for
the rest, is the honest scope — attempting all five in one pass risked
landing several half-done and untested implementations of exactly the
controls this item exists to make trustworthy.
