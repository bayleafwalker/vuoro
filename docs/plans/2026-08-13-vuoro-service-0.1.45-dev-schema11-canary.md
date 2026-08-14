---
doc_id: vuoro-service-0.1.45-dev-schema11-canary
status: final
recorded_at: 2026-08-13
---

# Vuoro service 0.1.45 dev schema-11 canary record

This is the durable Vuoro-side evidence record for the development-only
activation of session-completion serving. Appservice owns desired state,
secrets, database identities, migrations, and rollout. Its Git history remains
the source of truth for those changes.

## Released artifact

- Release tag: `vuoro-service-v0.1.45`.
- OCI artifact:
  `ghcr.io/bayleafwalker/vuoro-service@sha256:9ce139991c376855448134544b1d3cc7a5c6bdb1ea4f1aa5acff7509e9141ed3`.
- The release attestation was verified before dev activation.

## Dev activation evidence

Appservice PRs #1476, #1477, #1479, and #1480 supplied the additive
identities/secrets, drained `vuoro-dev`, staged the migration, and restored
the service after the migration gate. The drain event was recorded at
`2026-08-13T17:30:11Z`.

The completed CNPG backup was
`vuoro-dev-execution-schema3-20260813t172309z` (UID
`0a5411f2-3d2e-469f-9ec0-8d62d683b05e`, backup ID `20260813T173412`), started
at `2026-08-13T17:34:12Z` and stopped at `2026-08-13T17:34:20Z`, with begin
and end WAL `000000010000000000000017`.

The preflight reported zero active sessions, zero dispatch roots, zero
nonterminal records, zero other clients, and zero retained actions/events.
After activation, the migration ledger is present through schema 11 and the
dev service handshake reports:

- `vuoro-service` version/release `0.1.45`;
- compatible work, execution, knowledge, and audit domains;
- schema version `11`; and
- exactly 84 catalog operations at revision
  `fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`.

## Authority-separation evidence

The dev privilege matrix passed its intended boundaries:

| Identity | Completion access | Queue lifecycle mutation | Schema / migration ledger |
| --- | --- | --- | --- |
| Queue runtime | Existing runtime surface only | Existing runtime surface only | Denied |
| Completion ingest | Append/projection only | Denied | Denied |
| Completion read | SELECT only | Denied | Denied |
| Migration | Migration 011 only; never served | Not a runtime identity | Required only for migration/ledger work |

The service rejects missing, empty, or equal runtime/ingest/read DSNs before
constructing the ActionQ application, so neither completion path can fall back
to the queue-runtime connection.

## Remaining gates

The structural canary is complete. The positive end-to-end session-completion
operation remains pending a provisioned non-self-asserted identity authority.
This must be exercised with the real authority boundary; it is not acceptable
to create a bypass or a test-only served endpoint.

## Shared production activation

The operator explicitly approved the shared activation after the dev evidence.
Appservice then drained `vuoro-shared` at `2026-08-13T18:23:38Z` and completed
the backup `vuoro-shared-execution-schema10-20260813t182338z` (UID
`7ea6fea5-a905-4a89-a79c-333ca2ac44f5`, backup ID `20260813T183614`). It ran
from `2026-08-13T18:36:14Z` to `2026-08-13T18:36:25Z`; both begin and end WAL
are `000000010000000500000016`.

The exact shared preflight found the schema ledger at versions 1 through 10,
seven retained actions, 1,192 retained events, and zero active sessions,
dispatch roots, nonterminal records, or other clients. Migration applied
schema 11 with an empty retry set. The post-migration ledger is exactly 1
through 11 and the retained action/event counts remain 7/1,192.

Both the `vuoro-service` and `actionq-db-proxy` containers were rolled out to
the same attested 0.1.45 digest:
`sha256:9ce139991c376855448134544b1d3cc7a5c6bdb1ea4f1aa5acff7509e9141ed3`.
The shared handshake reports four compatible domains, schema 11, and exactly
84 catalog operations at revision
`fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`.
Authority-negative verification left no forbidden privilege residue: the
completion ingest/read identities cannot mutate queue lifecycle state, create
schema objects, or write the migration ledger.

Runtime activation is complete in dev and shared. Positive end-to-end
session-completion canaries have passed in both environments through the
provisioned non-self-asserted identity authority. Cleanup/revocation structural
verification is also complete, with no authority-negative residue. P2.2
schema-runtime consumer migration is independent: kctl 0.1.3, auditctl 0.1.2,
and ActionQ 0.1.22 are released candidates whose Vuoro composition acceptance
is complete. The accepted composition retains the same 84-operation catalog
revision and requires no runtime migration.

## 0.1.46 release and deployed canaries

`vuoro-service-v0.1.46` is published from source
`6bc23212edd611965f067781fb6c6af090ac1ed5`. The released wheel SHA-256 is
`978ef5a764932957636f9e6915e92f75ec773967f15a16dc5f9834a5ed71e938`; the
provenance verification passed. The attested OCI artifact is
`sha256:aeeb8088b8485c9637526b63d8557a68db618772979330ed5950f0e09c4a0f5c`.

Appservice deployed this digest through dev Flux revision `648be1` (canary
PASS) and shared Flux revision `352b32` (deployment PASS). Both handshakes
report compatible domains, schema 11, and exactly 84 catalog operations at
revision `fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`.
The retained database counts are dev queue `0` / completion `1`, and shared
queue `7` / events `1,192` / completion `1`. P2.2 is now fully released,
composed, and deployed.
