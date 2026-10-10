# Recovery and declared-effect preview composition

The client 0.1.2 release carries the incident-local export-only recovery
contract in [native recovery](native-recovery.md). It cannot submit recovery
records or replace Sprintctl's domain-native delivery and reconciliation.

The service 0.1.92 composition consumes the published Sprintctl 0.18.0 wheel,
source `9a8d356a82039bdbd80828710bf9d2cd8bb2c094`, SHA-256
`3fcbdaa5b56d18fe8a5b744195b143f2bd57fe221123ba1d567a8c4b198e2850`.
Its immutable dependency remains adapter-kit 0.2.0. The workspace adapter-kit
0.2.1 version is not substituted into the owner release's dependency closure.
Auditctl 0.1.9 and schema-runtime 0.1.0 retain their existing immutable pins.

The released work catalog adds `work.effect.preview-v1`, a repository-scoped
read requiring `work.effect.get` authority and refusing an idempotency key.
Default output redacts paths; `disclose_paths: true` explicitly includes the
bounded declared paths. The preview reports existing intent and receipt
observations; it grants no acceptance, execution, publication or settlement
authority. The prior 79 work operation definitions are unchanged. The composed
catalog has 80 work and five audit operations, revision
`4230516fcb775d72e14dfa774c8395a9f50ad5b640ccd9134648a8c659f22af3`.

The installed-wheel gates verify wheel digests and versions, dependency
consistency, owner metadata, complete catalog revision and real shell
invocation. Disposable PostgreSQL HTTP histories verify preview redaction,
explicit path disclosure, authority refusal, forbidden idempotency keys and
unchanged intent state. Existing bound-proposal, evidence and attempt guards,
and explicit schema-21 to schema-22 role-separated migration remain covered.
Startup never migrates an owner schema. These are release compatibility
checks, not proof of a production deployment or an owner task's completion.
