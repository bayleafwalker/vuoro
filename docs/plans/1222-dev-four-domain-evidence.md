---
doc_id: 1222-dev-four-domain-evidence
status: final
supersedes: null
---

# #1222 vuoro-dev four-domain evidence bundle

Owner: vuoro service evidence. Records handshake/catalog/invocation/decision
evidence across all four served domains (work, execution, knowledge, audit)
on `vuoro-dev`, per sprintctl #1164 gate-evidence ledger row 8.
Non-scope: production promotion (see #1223 / vuoro-shared instead).

## Identity gap found and closed

`vuoro-dev`'s identity registry (`vuoro-identities` Secret) previously had a
single bootstrap identity scoped to `work:read` only — insufficient to
exercise accepted decisions in execution/knowledge/audit. Added a second,
disposable identity (`agentops:vuoro-dev-four-domain-evidence`) with the
authorities needed across all four domains, via a normal SOPS-encrypted
commit to the `appservice` repo (`vuoro-identities.secret.yaml`), reconciled
with `flux reconcile kustomization vuoro-dev --with-source`, followed by a
`kubectl rollout restart` (the identity registry loads once at process
start, not per-request). This only touches `vuoro-dev` — `vuoro-shared`
(production) is untouched.

## Handshake evidence

`GET /api/meta/v1/handshake` (unauthenticated):

```json
{
  "environment": {"name": "vuoro-dev", "environment_class": "development"},
  "catalog_revision": "2e315de32d80f53486377349030f72aa7c2a8355ca87bb17ef9428b7adfdbedd",
  "compatibility": {
    "state": "compatible",
    "domains": {
      "work": {"state": "compatible"},
      "execution": {"state": "compatible"},
      "knowledge": {"state": "compatible"},
      "audit": {"state": "compatible"}
    }
  }
}
```

All four domains report `compatible` with no reasons.

## Catalog evidence

`GET /api/catalog/v1` (protocol header `X-Vuoro-Client-Protocol: 1`) returned
the full 39-operation catalog spanning all four `owning_domain` values
(`work`, `execution`, `knowledge`, `audit`), each operation carrying an
explicit `required_authority`.

## Invocation / decision evidence

`POST /api/invoke/v1`, one accepted and one rejected decision per domain
(full request/response bundle in this session's evidence run; summarized
here):

| Domain | Operation | Kind | HTTP | `status` | `error.code` |
| --- | --- | --- | --- | --- | --- |
| work | `work.read.sprints` | accepted | 200 | accepted | - |
| work | `work.claim.start` | rejected (limited identity) | 403 | rejected | `authority-required` |
| execution | `execution.action.list` | accepted | 200 | accepted | - |
| execution | `execution.action.claim` | rejected | 400 | rejected | `idempotency-key-required` |
| knowledge | `knowledge.candidate.list` | accepted | 200 | accepted | - |
| knowledge | `knowledge.candidate.approve` | rejected | 400 | rejected | `idempotency-key-required` |
| audit | `audit.observation.list` | accepted | 200 | accepted | - |
| audit | `audit.observation.submit` (empty args) | rejected | 400 | rejected | `idempotency-key-required` |

Two distinct, stable error surfaces observed across domains:
`authority-required` (identity lacks the operation's declared
`required_authority` — tested with the original limited bootstrap identity
against a work-domain mutating operation) and `idempotency-key-required`
(mutating operations across execution/knowledge/audit uniformly require an
idempotency key even before touching domain state). Both are structured,
machine-readable `invocation-result/v1` envelopes (`status`, `error.code`,
`error.message`), not raw exceptions or inconsistent shapes across domains.

`work.read.sprints` and `execution.action.list` returned empty results —
expected, since `vuoro-dev` is the ephemeral, non-authoritative environment
(no promoted work data lives here; that's `vuoro-shared`'s role per #1223).

## Gate status

Row 8 ("vuoro-dev four-domain evidence") of sprintctl's
`docs/plans/1164-gate-evidence-ledger.md` is satisfied by this record.
