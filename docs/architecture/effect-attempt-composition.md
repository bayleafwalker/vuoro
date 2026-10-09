# Effect attempt composition

Service 0.1.91 consumes the released Sprintctl 0.17.0 wheel and its schema-22
attempt ledger. The five additive `work.effect.attempt-*-v1` operations use the
existing `work.effect.mark-applied` authority. Audit and shared dependency pins,
identity scopes and existing operation descriptors retain their prior contracts.
The resource-disabled composition has 79 work operations and five audit
operations. The resource-enabled owner additionally exposes its three resource
operations.

An authorization binds an accepted intent, complete frozen Release, provider
operation and exact target to the authenticated principal, workspace, client,
grant and repository. A fresh redemption returns permission once. A historical
retry reports its committed receipt with `dispatch_permitted: false`. Sealing
records unused authorization; reporting records an applier's immutable PR
claim. These receipts distinguish authorization, consumption and authored
outcome claims. They do not establish independent provider execution or terminal
completion. The existing evidence evaluator v1 continues to report unsupported
execution coverage; consuming these facts requires a separate additive contract.

## Explicit schema transition

Startup checks compatibility and performs no DDL. Service 0.1.90 accepts schema
21 and refuses schema 22; service 0.1.91 refuses schema 21. The mandatory CI
upgrade gate starts from the actual hash-pinned old owner and service wheels,
preserves accepted intent/evidence and audit rows, applies migration 22 exactly
once, and exercises the new attempt history using the runtime role. Both runtime
roles must lack DDL and membership in either migration role.

A deployment must quiesce every old writer before explicit migration with the
released owner's migration role. Capture a completed backup and the current
schema, immutable image, catalog and identity revision first. After migration,
verify the ledger and preserved rows, deploy the matching immutable service
image, and qualify runtime DML, DDL refusal and ordinary served continuity.
Existing processes do not gain a new startup check automatically. After schema
22 is installed, recovery uses a compatible forward image. Restoring a database
backup is a separate coordinated operation, rather than a ledger rewrite or an
old-image rollback.

Provider instrumentation, independent terminal collection, inventory import,
attribution, cutover and soak remain subsequent S4 work.
