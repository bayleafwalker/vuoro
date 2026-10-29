# Vuoro service

Modular HTTP and process shell that composes immutable, released owner
adapters. Domain state machines and migrations remain in their owner packages.

Service 0.1.100 pins Sprintctl 0.25.0 and requires work schema 29. Startup
checks compatibility without migrating. Run the released owner's explicit
migration command with a migration role before starting the new composition;
the serving runtime role remains unable to execute DDL.

Schema 29 preserves existing leases with an unknown settlement basis. An
outcome reported for such a lease is retained as rejected with
`lease-basis-unavailable` and creates no decision. Drain active work before
upgrading and acquire new leases through the current owner after migration.
The upgrade gate exercises actual released service 0.1.97/schema 27 and
service 0.1.99/schema 28 closures, including runtime-role and old-binary refusals.
