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
| `unknowns` | Everything the operation could not establish. A non-empty list downgrades `ok` to `attention` in every operation, so an unknown is never reported as a pass. |

Every subcommand takes `--pins`, `--attestation`, `--work-ref` and `--output`.

An operation run off-image (on a workstation, from a source checkout) has no
`installed-composition.json`, so `installed-composition-attestation-absent` is
always among its unknowns and its status is at best `attention`. Pass
`--attestation` with the attestation of the image being diagnosed to compare
it with the pins and allow `ok`.

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
| `safe-to-seal` | Never redeemed, and exactly one journal record links to the intent revision with no transmission marker for that operation. |
| `provider-outcome-needed` | Redeemed without a report. Push authorizations never take a report. It also covers every unredeemed attempt whose journal link is absent, ambiguous or carries a transmission marker (flagged as an anomaly), and every attempt checked without a journal. Absence of journal evidence is not evidence of no transmission. |
| `closed-unused` | Already sealed without redemption. |

Sprintctl has no attempt list operation, so enumeration is limited to the
supplied ids, and the result says so in `unknowns`. To compensate, the result
also lists unsettled journal effects that match no supplied attempt.

### `provider-outcome-check`

```
vuoro-ops provider-outcome-check (--profile P | --attempts-export F) --attempt-id A \
  [--journal DIR] [--artifact-root DIR] [--remote URL] [--github-token-env VAR] \
  [--github-api URL [--allow-token-for-github-api]]
```

This check establishes one attempt's provider outcome without re-dispatching:

- **Branch.** `git ls-remote --refs --end-of-options <remote> refs/heads/<branch>`
  is compared with the authorized `commit_sha`. Option-shaped remotes and
  branches are refused.
- **Pull request.** `GET /repos/{owner}/{repo}/pulls?head=owner:branch&state=all`
  is compared on head SHA and base branch, for both push and PR attempts.
  The token from `--github-token-env` is only sent to `https://api.github.com`,
  unless `--allow-token-for-github-api` explicitly allows another
  `--github-api`. That flag is refused unless `--github-api` is https with no
  userinfo. Redirects are never followed.
- **Signed artifact.** For the protected Git path from #220, when the
  artifact root is supplied, the journaled `LOCAL_READY` bundle digest is
  re-hashed and `git bundle list-heads` must contain the journaled commit.

A missing or moved ref never settles to "not applied" on its own:

| Operation | Observation | Outcome |
| --- | --- | --- |
| push | branch head is the authorized commit | `applied` |
| push | branch absent, a merged PR's head is the authorized commit (branch deleted after merge) | `applied` |
| push | branch absent otherwise | `unknown` |
| push | branch head is another commit (moved after the push, or another actor) | `needs-review` |
| PR | a PR from the branch has the authorized head and base | `applied` |
| PR | a PR exists with another head or base | `needs-review` |
| PR | no PR ever, branch at the authorized commit | `partially-applied` |
| PR | no PR ever, branch absent | `not-observed` |
| PR | no PR ever, branch elsewhere | `needs-review` |
| any | a lookup failed | `unknown` |

`next_step` states the owner action as text.
An applied, unreported PR is an applier's `attempt-report-applied-v1`. A
redeemed, unapplied attempt needs a new intent revision, because replay never
re-grants dispatch. Only an unredeemed `safe-to-seal` attempt that is
`not-observed` is described as sealable by its principal. `unknown` and
`needs-review` say not to seal, report or re-dispatch. `vuoro-ops` performs
none of these.

### `role-probe` and `catalog-probe`

```
vuoro-ops role-probe    --profile P --repo-id R
vuoro-ops catalog-probe --profile P [--expected-from-image IMG | --expected-catalog-revision REV --expected-operation-count N]
```

Both probes use a `vuoro-client-profile/v1` served profile and its 0600
`file:` credential. The endpoint must be `https`, or `http` to exactly
`localhost`, `127.0.0.1` or `::1`, without userinfo. Redirects are never
followed, so the bearer credential is only sent to the profile's endpoint. Only handshake and catalog `GET` requests are sent, plus
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
  --target-schema N --migration-job-state STATE \
  [--current-schema N | --profile P --served-image IMG] [--domain work|audit]
```

The preflight reads each image's admitted startup range from the pinned
owners inside the image: Sprintctl's `MINIMUM/MAXIMUM_SCHEMA_VERSION` and
Auditctl's `MIN/MAX_RUNTIME_SCHEMA_VERSION`. It also reads each image's
installed distributions and composition attestation. The current schema is
taken from the operator, or deduced: a served handshake reporting the domain
compatible, from the `--served-image` that admits exactly one version, proves
that version. Without `--served-image`, the current schema stays unknown. The
prior image's range is never borrowed for it.

`--migration-job-state` is the observed state of this rollout's migration Job:
`not-created`, `in-flight`, `complete`, `failed`, or `absent` (the Job cannot
be observed, so whether it ran is unknown). The recovery class is computed
from these values and never accepted as input. Any unknown input (the prior
range, the current schema, or a Job that is `in-flight` or `absent`) yields
no class and status `error`, never advice:

| Class | When |
| --- | --- |
| `cancel` | The current schema is below the target, the Job is `not-created` or `failed`, and the prior image admits the current schema. If the Job reports `complete` while the schema is below the target, no class is given. |

"Job `failed` and schema below target, so `cancel`" assumes every migration
step is transactional: a failed step rolled back and left no partial change.
Neither owner publishes machine-readable metadata confirming that, so this
case always carries the unknown `non-transactional-step-possible`, and its
status is `attention`. Confirm that the owner's steps are transactional before
treating the rollout as cancelled. A Job that is `not-created` or `failed`
while the schema is already at or above the target is reported as the anomaly
`job-state-inconsistent-with-schema`. That state means an earlier rollout, an
out-of-band migration or a commit before the failure, and a person must
confirm which. The exception is a no-op redeploy: the Job is `not-created`,
the current schema equals the target, and the `--served-image` range admits
the target. In that case no anomaly is reported. Without `--served-image`
this cannot be established, so the anomaly stays.
| `image-revert` | The target is committed and the prior image's range still admits it. |
| `forward-fix-or-restore` | The committed schema is outside the prior image's range. Recovery is a compatible forward image or a coordinated restore, not an old-image rollback. |

`recovery_class` is the class at the observed state, and
`recovery_class_after_commit` is the class once the target commits. The gates
(`candidate_admits_target`, `prior_admits_current`, `prior_admits_target`,
`target_not_behind_current`, `migration_job_settled`) are reported
individually. A failed `candidate_admits_target` or `target_not_behind_current`
blocks the preflight with `error`. So does a failed `prior_admits_current` while
the schema is still below the target, because then the prior image must be
what serves it. After a commit, a prior image that does not admit the current
schema is what `forward-fix-or-restore` means. `--run-startup-proof` wraps
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
are refused. It initializes a throwaway PostgreSQL cluster in a fresh
temporary directory. The binaries must report major version 16 (from
`--pg-bin`, `VUORO_PG_BIN`, `PATH`, the Nix store or `/usr/lib/postgresql/16`);
any other version is refused. The cluster has `listen_addresses=''`, so there
is no TCP. Its Unix socket lives in a private 0700 directory. For an
`--owner-image` check, that directory is bind-mounted into a container that
runs with no network as the invoking uid.

The command recreates every role the dump owns or grants to, as non-superuser
roles. It then restores with `--exit-on-error --single-transaction` as
`vuoro_ops_restorer`, a non-superuser that is a member of those roles and owns
the scratch database. Object owners get `CREATE` on its `public` schema,
because changing an object's owner needs it. The configured runtime roles
(`--role`, default `<domain>_runtime`) never get that grant. A dump in which a
runtime role owns a `public` object therefore fails to restore rather than
handing that role DDL privileges. No role gets `CREATE` on the database: only
the restorer, which owns it, needs that to change a schema's owner.
Superuser-only statements in a dump therefore fail the restore instead of
running on the host: `COPY ... TO PROGRAM`, untrusted languages, event
triggers and most extensions. pg_restore itself still parses the archive, so
restore only dumps from a known producer.

The pinned owners' read-only startup checks then run as each domain's runtime
role (`<domain>_runtime` by default). A runtime role missing from the dump is
never replaced by a superuser: that domain is reported unchecked, as an
unknown with status `attention`.
These are Sprintctl `compatibility_handshake` and Auditctl
`check_compatibility`, the same calls `create_composed_app` makes. The cluster
is then deleted.

The check runs in-process when the pinned distributions are installed at the
pinned versions, or inside `--owner-image`. Otherwise it is an unknown. The
result carries the dump digest (hashed as a stream), TOC size, schemas,
created roles, the restore role, restore exit status, per-domain
compatibility with the owner's schema range, and `live_database_touched:
false`. CI sets `VUORO_OPS_REQUIRE_PG16=1`, so these tests fail rather than
skip when PostgreSQL 16 is missing.

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
