# MI-1 portable provider ingestion conformance

The P3 provider replay gap is tested through a neutral binding over synthetic
GitHub check/workflow/PR and Claude outcome events. The current pure decoder
produces observation drafts; it invokes no transport and grants no authority.
The same three histories run on existing reference chain/idempotency primitives
and the published native Sprintctl owner. Tests use no provider table names,
row counts or owner-storage injections.

A lost reply after committed append is followed by reconnect and exact retry:
the registered identity, receipt and tail must be unchanged. The PG reconnect
closes the first connection and constructs a new WorkApplication; reference
reconnect is a facade no-op and is not a persistence claim. Changed observed
artifact content under the same provider delivery key must be refused as
`idempotency-conflict`, with the original receipt/history/identity preserved.
A different delivery extends the chain, and retrying the old delivery must
return its old receipt while leaving the newer tail untouched.

All cases preserve pending work. Provider claims have null grant/confirmation
and unverified assurance; absent build/session/check/instruction values remain
`unknown`. The supplied artifact observation is not asserted to be a protected
verifier's result. No provider success becomes a Decision.

Two reference mutants forget the first delivery binding. One duplicates a
retry; the other permits changed content instead of refusing. The same
portable assertions reject both. These are bounded sequential examples, not
a concurrency or formal proof. Actual owner contention and carrier reply-loss
tests remain in Sprintctl PR125/127.

The existing CI published-owner job now includes this test file. It installs
the unchanged SHA-verified Sprintctl0.12.0 composition pin and mandates the
PostgreSQL binding. A skipped owner, reference pass or generic ledger test
cannot qualify this provider-specific contract. Exact results are recorded in
the [context](../../verification/contexts/mi1-provider-ingestion.json) and
[result](../../verification/results/mi1-provider-ingestion-2026-10-06.json).

P2's release/raw-artifact/protected-verifier link and the actual cross-harness
Track B proof remain open. This adds no runtime store, driver, evaluator,
Decision path, production code or release pin.
