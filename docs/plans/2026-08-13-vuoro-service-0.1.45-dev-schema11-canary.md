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

`vuoro-shared` is unchanged: it remains on service 0.1.44 and schema 10. A
fresh shared backup and quiescence preflight, followed by explicit operator
approval, are required before any shared migration or image promotion.
