# MI-1 protected check execution and native evidence request

Bounded continuation of agentops#2613, governed by MI-1 at
`f9cadd85b35cd28717c5ab3fc24409bcb0f7b98f` and TS-1/2/16. PR189 consumes
protected assertions; this increment actually executes the existing trusted
reconciler's three checks: patch text/path safety, clean checkout/application,
and staged content/path/mode policy. These are artifact safety checks, not a
claim of functional correctness or execution of proposer-supplied commands.

Reuse one validation path for verification and application. Verification does
not sign, push, open a PR, accept an intent or finish work. A trusted runtime
factory supplies repository mapping and policy. Its revision hashes installed
validation source bytes, normalized per-repository policy and observed Git
version. This records which checks ran; it is not remote execution attestation.
The source authenticates the verifier through its existing transport, checks a
proposed exact canonical intent and current Release/work edit before and after
execution, and resolves the verifier-owned native run. It emits the existing
owner append request and exact run binding for Sprintctl's durable evidence
queue/sync. It does not build another outbox, append loop or acceptance path.

The trusted CLI `verify` outputs this request/binding. Preserve that exact
output before capture; retry the captured request through the existing native
producer. A competing append can cause an explicit chain conflict; use the
producer's existing confirmed-conflict correction rather than guessing a new
request after an unknown result. Acceptance is a separate operator action
using the confirmed run/item reference. The owner rechecks binding at acceptance.

Failure oracles: binary/control/path-policy violation, nonapplicable diff or
unlisted repository emits no success request; work edit during checkout refuses;
wrong run principal/workspace refuses; no signing or forge write during verify;
positive real local Git validation emits three revision-bound passed checks.
Actual released-owner integration queues/synchronizes that generated request,
accepts it and reconciles after reconnect. Interrupt/replay delivery reuses the
producer's established semantics. No commissioned HTTP/commercial harness or
production schema rollout claim. P2 stays active until runtime acceptance.
