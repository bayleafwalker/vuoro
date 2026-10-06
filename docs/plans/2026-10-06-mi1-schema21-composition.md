# MI-1 schema-21 service composition

Continuation of agentops#2613 under MI-1 revision
`f9cadd85b35cd28717c5ab3fc24409bcb0f7b98f`. Source service 0.1.86 consumes
immutable, downloaded, SHA-verified and exact tagged-source attested releases:

| Distribution | Version | Source revision |
| --- | --- | --- |
| sprintctl | 0.13.1 | 208abe080ed2331eb6bf7c1a16774b5721fd0d66 |
| auditctl | 0.1.9 | 8fd32c1a1d75db055d09b00ee464874d402213ac |
| vuoro-adapter-kit | 0.2.0 | 686039a9dafaa454b2db8323c795ba91a1f59cbf |
| vuoro-schema-runtime | 0.1.0 | a002e503dc1fa2f04858b04b581f5fcdfa0e7f3c |

Both released owners require the same immutable kit URL/hash. Audit retains
schema-runtime 0.1.0 and declares database schemas 2 through 3 compatible.
Work requires exactly database schema 21. Source workspace shared packages may
have later versions; they are not the installed production closure. The v3
manifest retains the same two owner descriptors and dependency edges. Catalog
membership without optional resource storage remains 71 work plus five audit
operations. Reviewed Sprintctl wire changes are optional release/proof metadata
and the optional native receipt reference on the existing private accept method,
not a new public apply operation. Existing released-wheel guards keep exact
metadata/catalog fingerprints for this reviewed release.

Service startup stays read-only and never migrates. This pin does not alone
upgrade shared state or commission private actors. Publish the immutable image
and attest/inspect its installed closure first. Appservice owns a separate,
concrete schema-21 cutover: verify actual old schema, writer inventory and backup
health; stop every old writer across the exact-schema boundary; run the owner
migration as the migration role; verify runtime privileges and schema 21; replace
the old image and prove readiness/served continuity before enabling separately
scoped verifier/applier identities. An old already-running handler does not gain
new proof guards from DDL alone. Recovery preserves history; never rewrite a
migration ledger or reuse an old immutable release tag. Frozen rebuild/recovery
is separate from steady-state rollout.

Verification uses service/package gates, released work/audit and combined
catalog invocation checks, actual disposable owner PostgreSQL contracts, image
provenance/pip consistency and strict MCP client tests. No new executor, public
merge rights or hosted harness claim. Protected local CLI configuration and real
issuer-authenticated proof remain separate from this service packaging increment.
