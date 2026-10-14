# Diagnostic operations (`vuoro-ops`)

Recurring operator interventions are now bounded, versioned operations. Each
one only observes and classifies, and returns one structured result linked to
the work it concerns and to the release composition it was computed against.
None of them dispatches provider work, redeems, seals or reports an effect
attempt, migrates a schema, or connects to a live database.

## Placement

`vuoro-ops` is a console script of `vuoro-service`
(`packages/vuoro-service/src/vuoro_service/ops/`). `AGENTS.md` assigns
operational entrypoints and compatibility gates to `vuoro-service`, and the
service wheel is installed in every immutable service image. The operator
therefore gets the same command on a workstation and inside the image whose
composition it diagnoses. The reconciler CLI was rejected as the home:
`vuoro-reconciler` is the trusted executor, and diagnostics must not share a
process or entrypoint with execution authority. The reconciler's journal is
read as data through SQLite `mode=ro`. `vuoro-service` gains no runtime
dependency, the composition gates are unchanged, and the MCP edge is untouched.

## Result contract: `vuoro-diagnostic-result/v1`

Every subcommand prints one JSON document (schema:
`vuoro_service/ops/diagnostic-result-v1.schema.json`, validated before it is
printed):

| Field | Meaning |
| --- | --- |
| `operation`, `version` | Operation name and its integer contract version (all `1`). |
| `status` | `ok`, `attention` (a finding needs a person) or `error` (could not establish). Exit codes are 0, 1 and 2. |
| `composition` | `pins_sha256` of `adapter-pins.json`, its locks and runtime descriptors, whether it loads under the composition gate, the local `vuoro-service` version, and the installed attestation (`installed-composition.json`). The attestation is compared with the pins when present and reported as an unknown when absent. |
| `work_refs` | `{kind, id}` links, such as `effect-attempt`, `effect-intent` (`id@revision`), `work-item`, `incident`, `environment`, `candidate-image`, `prior-image` or `dump` (`sha256:`). The operator can add more with `--work-ref KIND:ID`. |
| `observed_at` | UTC observation time. |
| `result` | Operation-specific body (below). |
| `unknowns` | Everything the operation could not establish. An unknown is never silently turned into a pass. |

Every subcommand takes `--pins`, `--attestation`, `--work-ref` and `--output`.

## Operations

### `uncertain-effects`

```
vuoro-ops uncertain-effects (--profile P | --attempts-export F) \
  --attempt-id attempt_... [--attempt-ids-file F] [--journal DIR] [--repo-id R]
```

Each attempt is read through the read-only owner operation
`work.effect.attempt-get-v1` (or from exported get results). It is joined to
the reconciler's execution journal by intent id and revision, with the
provider operation mapped `push_branch` to `branch-create` and
`open_pull_request` to `pr-create`. The journal reader checks the v1 catalog,
the contiguous event chain from `BOUND` and every stored row digest.
Tampering makes the journal an unknown, never a pass. Classes:

| Class | Rule |
| --- | --- |
| `reported` | Redeemed, and `application_report_received` is recorded. This is an applier claim, not provider proof. |
| `safe-to-seal` | Never redeemed, and a supplied journal holds no transmission marker for that intent and operation. |
| `provider-outcome-needed` | Redeemed without a report. Push authorizations never take a report. It also covers an unredeemed attempt with a journal transmission marker (flagged as an anomaly) and an unredeemed attempt checked without a journal. |
| `closed-unused` | Already sealed without redemption. |

Sprintctl has no attempt list operation, so enumeration is limited to the
supplied ids, and the result says so in `unknowns`. To compensate, the result
also lists unsettled journal effects that match no supplied attempt.

### `provider-outcome-check`

```
vuoro-ops provider-outcome-check (--profile P | --attempts-export F) --attempt-id A \
  [--journal DIR] [--artifact-root DIR] [--remote URL] [--github-token-env VAR]
```

This check establishes one attempt's provider outcome without re-dispatching:

- **Branch.** `git ls-remote --refs <remote> refs/heads/<branch>` is compared
  with the authorized `commit_sha`.
- **Pull request.** `GET /repos/{owner}/{repo}/pulls?head=owner:branch&state=all`
  is compared on head SHA and base branch.
- **Signed artifact.** For the protected Git path from #220, when the
  artifact root is supplied, the journaled `LOCAL_READY` bundle digest is
  re-hashed and `git bundle list-heads` must contain the journaled commit.

Outcomes are `applied`, `not-observed`, `partially-applied` (branch present,
no PR), `conflict` or `unknown`. `next_step` states the owner action as text.
An applied, unreported PR is an applier's `attempt-report-applied-v1`. A
redeemed, unapplied attempt needs a new intent revision, because replay never
re-grants dispatch. An unredeemed, unobserved attempt can be sealed by its
principal. `vuoro-ops` performs none of these.

### `role-probe` and `catalog-probe`

```
vuoro-ops role-probe    --profile P --repo-id R
vuoro-ops catalog-probe --profile P [--expected-from-image IMG | --expected-catalog-revision REV --expected-operation-count N]
```

Both probes use a `vuoro-client-profile/v1` served profile and its 0600
`file:` credential. Only handshake and catalog `GET` requests are sent, plus
invocations whose catalog semantics are `read`, without an idempotency key.
The reader refuses anything else.

The handshake is compared with the profile and the pins: environment and
class, client protocol, overall and per-domain compatibility, the local
service version, each pinned domain's API version, and served domains that
are not pinned. The per-domain `schema_version` is the owner's own record
label (Sprintctl reports its handshake document schema), so it is shown and
not compared.

`role-probe` calls `work.identity.current`. For each required authority in
the profile, it then calls the first argument-free read operation that
requires it. An authority that no such operation exercises is reported as
`not-observable-read-only`. `catalog-probe` checks the served revision against
the handshake, the operation and domain counts, and operations from unpinned
domains. It compares them with an expected catalog that the operator supplies
or that is derived inside an immutable image (the owners' own registration,
as `scripts/validate_released_catalog_composition.py` does). It also compares
that image's manifest digest with the local pins. Mismatches are structured
`{kind, expected, observed, severity}`.

### `migration-preflight`

```
vuoro-ops migration-preflight --candidate-image IMG@sha256:... --prior-image IMG@sha256:... \
  --target-schema N [--current-schema N | --profile P --served-image IMG] [--domain work|audit]
```

The preflight reads each image's admitted startup range from the pinned
owners inside the image: Sprintctl's `MINIMUM/MAXIMUM_SCHEMA_VERSION` and
Auditctl's `MIN/MAX_RUNTIME_SCHEMA_VERSION`. It also reads each image's
installed distributions and composition attestation. The current schema is
taken from the operator, or deduced: a served handshake reporting the domain
compatible, from an image that admits exactly one version, proves that
version. The recovery class is computed from these values and never accepted
as input:

| Class | When |
| --- | --- |
| `cancel` | The current schema is below the target: no change has been applied yet. |
| `image-revert` | The target is committed and the prior image's range still admits it. |
| `forward-fix-or-restore` | The committed schema is outside the prior image's range. Recovery is a compatible forward image or a coordinated restore, not an old-image rollback. |

`recovery_class` is the class at the observed state, and
`recovery_class_after_commit` is the class once the target commits. The gates
(`candidate_admits_target`, `prior_admits_current`, `prior_admits_target`,
`target_not_behind_current`) are reported individually. A failed gate blocks
the preflight with `error`. `--run-startup-proof` wraps
`scripts/verify_pre_migration_startup.py`, but only where it applies. That
specialist proof (#2092) has its states fixed at work 5 to 6 and requires
podman, so any other target reports `not-applicable` instead of a misleading
pass.

### `recovery-export`

```
vuoro-ops recovery-export --incident-id ID [--root DIR]
```

This command wraps `vuoro_client.recovery.RecoveryLog.export()` and adds the
record count, the kinds, a canonical `records_sha256`, the count of requested
commands awaiting owner review, and the composition stamp. It never begins
(creates) a namespace. Requested commands remain text and are never run.

### `isolated-restore`

```
vuoro-ops isolated-restore --dump FILE.dump [--owner-image IMG] [--role work=work_runtime] [--schema work=work]
```

The command accepts a plain `pg_dump -Fc` file and never a connection string.
Age-encrypted input (an `age-encryption.org/` header) and non-custom archives
are refused. It initializes a throwaway PostgreSQL cluster (PostgreSQL 16 from
`VUORO_PG_BIN`, `PATH`, the Nix store or `/usr/lib/postgresql`) in a fresh
temporary directory. The cluster listens only on 127.0.0.1 at a free port.
The command recreates every role the dump owns or grants to, restores with
`--exit-on-error --single-transaction`, and runs the pinned owners' read-only
startup checks as each domain's runtime role (`<domain>_runtime` by default).
These are Sprintctl `compatibility_handshake` and Auditctl
`check_compatibility`, the same calls `create_composed_app` makes. The cluster
is then deleted.

The check runs in-process when the pinned distributions are installed at the
pinned versions, or inside `--owner-image`. Otherwise it is an unknown. The
result carries the dump digest, TOC size, schemas, created roles, restore exit
status, per-domain compatibility with the owner's schema range, and
`live_database_touched: false`.

## Verification

`packages/vuoro-service/tests/test_ops_*.py` covers the following:

- The envelope and its schema.
- Stamp comparison.
- Probe mismatch classes against a fake served Vuoro, including refusal of
  non-read operations.
- Every recovery class and gate, including unreadable images and the deduced
  current schema.
- Recovery export, including that a missing incident is never created.
- Attempt classification against a journal built with the reconciler's own v1
  DDL, including digest tampering and byte-identical reads.
- Provider outcomes against a real bare Git remote and a signed bundle.
- Isolated restore of a real `pg_dump -Fc` into a disposable PostgreSQL 16.
  These tests are skipped when no PostgreSQL binaries are present.
