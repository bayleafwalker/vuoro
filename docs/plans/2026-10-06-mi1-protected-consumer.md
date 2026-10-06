# MI-1 protected receipt consumer and publication preflight

Source implementation for agentops#2613, following Sprintctl PR128/129 and
MI-1 at `f9cadd85b35cd28717c5ab3fc24409bcb0f7b98f`. The existing protected
reconciler and operator acceptance path consume the binding; no new authority,
public apply path, policy evaluator or runner is introduced.

## Owner binding through the protected consumer

`SprintctlIntentSource` preserves the proposal's optional `release_digest` and
exact immutable acceptance metadata. Its private `accept` operation forwards an
explicit `verification_ref` when provided. The operator interface exposes
`--verification-run-id` and `--verification-item-id` together; a partial pair
refuses before discovery. Neither principal nor acceptor attribution is sent as
an owner wire argument. The owner authenticates the caller and receipt writer.

The reconciler independently checks every present protected receipt against the
actual UTF-8 unified diff, exact intent ID/revision/canonical digest and frozen
Release. It recomputes the compact sorted JSON receipt-body digest, verifies the
protected verifier equals the recorded acceptor, and requires bounded unique
passed checks with SHA-256 revisions. This check is in the kernel even if a
custom source's preflight does nothing. It verifies a protected assertion's
binding; it does not execute the named checks or attest their execution.

The native source freshly reads `work.effect.get-v1`, compares the exact stored
approval/content with the polled snapshot, reads `work.read.release` by item,
and compares a bound Release with the current work's `edit_revision` from
`work.read.item`. A required contract needs proof; malformed requirements fail
closed. Missing or changed current Releases refuse bound intents. A bound
custom source without preflight cannot publish. Visibly unbound legacy intents
retain their earlier behavior; this does not qualify them as MI-1 proof.

## External effect boundaries

Preflight runs before checkout, before publication lookup, immediately before a
branch push, and immediately before opening its PR. An edit during lookup or
local checkout refuses before a push. An edit after the push leaves that signed
branch visible, refuses PR creation, and keeps owner acceptance available for
inspection. The existing recovery mechanism still verifies a prior branch/PR
as this exact signed change before adopting it. No automatic merge is added.

These observations are not a transaction across the owner and forge. A change
can happen after any final read or during an external request; the owner also
rechecks its Release when recording application. The existing reporting and
recovery path remains necessary when an external effect exists without an owner
application record. Do not infer that a refused or failed outcome proves no
branch exists; the after-push case explicitly demonstrates the partial effect.

## Discriminating and released-owner evidence

The unchanged consumer at `1e947bbb55aeadd5dfc38216cc657f56915b4693` fails the
new stale-Release oracle: it signs/publishes the change and returns `applied`.
The fixed consumer refuses with no clone, push or PR. Additional cases mutate
artifact domains/hashes, intent bindings, check results/revisions, proof digest,
verifier identity, work revision and requirement types; each stops before
external writes. A custom no-op preflight cannot bypass raw receipt validation.

The actual native owner integration uses the SHA-verified released Sprintctl
0.13.1 wheel, source `208abe080ed2331eb6bf7c1a16774b5721fd0d66`, artifact
`4d8c4c334c15c65a81c2a89b34cfdf760eeff6ea7061f60e22bbe8a82de4260a`.
Its GitHub build attestation verifies the exact tag, source digest and signer
workflow. Proposer, verifier and applier have separate native principals and
narrow capabilities. A fresh owner connection/source reconstructs the stored
proof, reconciles to real local signed Git and records application. Work remains
pending because artifact acceptance/application is distinct from work settlement.
Two additional actual-owner histories edit work before and after the push.
All three pass on isolated, nonprivileged, loopback PostgreSQL 16.

These assertions are synthetic; they are not a commercial SDK execution or an
issuer-authenticated HTTP deployment proof. CI fetches the pinned released wheel
and runs these cases in a separate `lease_conformance_protected_ci` database,
after the existing deployed-owner scenarios. The service's current composition
is not changed by this test dependency. The previous 0.13.0 tag failed its
optional-client release gate and produced no wheel; patch 0.13.1 corrected that
gate without changing owner schema 21 semantics.

## Remaining MI-1 integration

Actually execute the declared protected checks and emit their native receipt,
prepare the compatible released service composition, coordinate schema 21
migration and replacement of every old runtime, then commission separately
scoped protected identities. The measured issuer currently has no private effect
actors. New private credentials must not reach an old handler lacking the proof
guard. P1 must reconstruct the explicit acceptance/Release/evidence links.
Real hosted and different commercial native harness runs, interruption/stale
proposal proof and the comparison window remain required. P2 stays active;
these source and owner tests do not close Track B or the resource-carrier gaps.
