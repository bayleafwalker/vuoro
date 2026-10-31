# Vuoro service

Modular HTTP and process shell that composes immutable, released owner
adapters. Domain state machines and migrations remain in their owner packages.

Service 0.1.103 is a source candidate pinning Sprintctl 0.28.0 and
vuoro-workspace 0.1.1. Sprintctl requires work schema 31; the workspace owner
keeps knowledge schema 1. The published owner also exposes an outcome-report
write operation and read-only lease currentness; currentness reports
`authorizes_effect=false` and grants no execution authority. The composition
retains principal continuation receipt assertions and workspace identity reads. A receipt does not prove native session
delivery, source verification or work-to-workspace provenance. Startup checks
compatibility without migrating. Run the released owner's explicit migration
command with a migration role before starting the new composition; the serving
runtime role remains unable to execute DDL. No deployment or field acceptance
is established by this source candidate.

Compared with the released 0.1.102 catalog, the 0.28 owner adds
`work.lease.currentness-v1` and the mutating `work.lease.report-outcome-v2`.
The latter requires `work:claim` and an explicit continuation checkpoint;
a stale or expired outcome may be retained as a rejected report without
settlement. The descriptors of `work.run.delegate-v1`,
`work.run.delegation-get-v1` and `work.run.delegation-revoke-v1` add only
the currentness operation to their closed delegation enums. The descriptor of
`work.run.continuation-delta-v1` removes a nested `$schema` declaration and
requires a nonempty `repo_id` in its returned checkpoint: this is a wire-schema
narrowing. In a disposable comparison both released owners refused empty-repo
run registration and accepted the named `global` repository; this is not a
general compatibility proof. The other 144 prior descriptors are unchanged.

Schema 29 preserves existing leases with an unknown settlement basis. An
outcome reported for such a lease is retained as rejected with
`lease-basis-unavailable` and creates no decision. Drain active work before
upgrading and acquire new leases through the current owner after migration.
The upgrade gate exercises actual released service 0.1.97/schema 27 and
service 0.1.99/schema 28 closures, including runtime-role and old-binary refusals.
