# Served maintenance capabilities

Vuoro exposes Sprintctl's maintenance-capability lifecycle by composing the
immutable Sprintctl 0.2.14 adapter release. Vuoro owns catalog transport,
identity and repository authorization, compatibility admission, and retry
transport. It does not interpret the maintenance envelope or decide a
lifecycle transition.

The checked-in work pin binds source commit
`b0123169a69e19c3ea3d7ef29b4cc6ed06409d29`, release
`vuoro-adapter-v1-b012316`, and wheel SHA-256
`b142a24c20079637b9c5aabe7df2c0df8ded1c290c14ee20326b7ebce005b030`.
Service startup verifies the local wheel bytes before importing
`sprintctl.vuoro_adapter:register_work_catalog`; startup never installs an
artifact or runs `sprintctl remote-schema migrate`.

The dynamically discovered operations are:

- `work.read.maintenance-capability`
- `work.maintenance.prepare`
- `work.maintenance.transition`
- `work.maintenance.recovery-record`

Clients invoke them through the ordinary schema-driven `invoke` method. The
operation definition, not Vuoro client code, supplies the input/result schema,
required authority, idempotency policy, and repository scope. This keeps
accepted, rejected, stale, duplicate, expired, aborted, revoked, and
incomplete owner outcomes intact. A stale catalog triggers the existing
refresh path; it does not cause an automatic mutation retry.

Recovery evidence remains non-authoritative. A retained incident record may
be submitted only as the immutable artifact reference required by
`work.maintenance.recovery-record`, with its record ID used as both request ID
and idempotency key. The operation requires `work:maintenance-audit` and its
result is schema-constrained to `authority=none`. Requested commands are audit
input for an operator; Vuoro never turns them into transitions or grants.
Reconciliation is an explicit `work.maintenance.transition` request whose
complete audit bundle is validated and decided by Sprintctl.

The project aggregate `work.project.next-work-explain` remains Vuoro #2076 and
is outside this release. Deployment, database migration, image publication,
and cluster reconciliation remain appservice-owned follow-up work.
