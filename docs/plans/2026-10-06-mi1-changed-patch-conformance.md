# MI-1 changed-patch conformance increment

This implements one P3 gap from the market integration milestone, without
changing production authority, persistence, packaging or deployment. P2's
released-artifact verification requirement remains open.

## Contract and oracle

`test_effect_authority_contract.py::test_changed_patch_cannot_reuse_approval`
changes only the UTF-8 patch bytes while holding the other proposal fields
fixed. The new proposal must have a distinct intent and canonical digest, no
acceptance and no application. Substituting the old accepted digest into the
new proposal's accept or mark-applied request must return
`effect-digest-mismatch`, preserving both proposals and pending work. Applying
the new proposal with its own digest before independent acceptance must return
`effect-invalid-transition`. A positive control independently accepts the new
digest without settling work.

The same test runs through the existing neutral binding on the actual
InMemoryIntentStore and, when configured, Sprintctl's published owner
WorkApplication on an isolated PostgreSQL database. It inspects no owner
storage layout. A deliberate reference mutant ignores the supplied digest;
the oracle rejects that mutant with `DID NOT RAISE`.

## Verification and qualification

Local focused result: 19 passed on the reference binding, including the
mutant. Local full suite: 1542 passed, four existing skips because the
disposable PostgreSQL binding was not configured. All four boundary wheels
built. These results qualify the reference case only.

The existing CI `settlement-scenario` job installs the SHA-verified composition
pin (Sprintctl 0.12.0, source
`f6936f410f41a7dfaf7e5a1390c552eb90954874`, wheel SHA-256
`e669d47f698b6d9509132e2f3dae17323d7b6c17b3c446a66b8172c026c4c5af`)
and runs this same test with `VUORO_AUTHORITY_TEST_PG_URL` on a disposable
loopback database. The owner qualifies only when that exact-head job succeeds;
a reference pass or an unconfigured owner skip cannot qualify it.

## Remaining coverage

| Milestone behaviour | Evidence | Scope / remaining work |
|---|---|---|
| Changed patch cannot reuse approval | New neutral changed-patch case and digest-ignoring mutant | Canonical intent includes patch bytes; released raw artifact / protected verifier / resulting bytes remain unproven. |
| Public caller cannot reach protected apply | Native commissioning receipt records HTTP 403; Sprintctl owner tests cover public effect denial | Still needs an explicit portable public/protected contract case. Trusted-side mark-applied tests are not public-route proof. |
| Provider ingestion replay and conflicts | Sprintctl PR125 and PR127 actual owner fault histories | Still needs a neutral ingestion contract binding, independent of owner storage layout. |

A canonical intent digest hashes the proposal's full canonical JSON, including
the patch. It is distinct from a raw artifact hash and from an observation
record hash. This increment does not claim those hash domains are
interchangeable, does not prove a protected verifier, and does not complete
P2, P3 or Track B.
