# Disposable native settlement demonstration

This optional distribution is a conformance consumer. It is not installed in
the production service image, and adds no owner API, runner, state store or
client transport behavior. Install the wheel in a fresh Python 3.12 environment;
its metadata binds released client 0.1.2, service 0.1.97 and Sprintctl 0.22.0
artifacts by URL and SHA256. PostgreSQL 16 tools and Git must be installed.

Run as a **non-root Unix user**. From this checkout, install the optional
consumer into its own environment (it is intentionally outside the development
workspace and production image):

```sh
uv venv /tmp/vuoro-demo-env
uv pip install --no-config --python /tmp/vuoro-demo-env/bin/python ./packages/vuoro-demo
/tmp/vuoro-demo-env/bin/vuoro doctor --pg-bin /path/to/postgresql16/bin
/tmp/vuoro-demo-env/bin/vuoro demo --pg-bin /path/to/postgresql16/bin --receipt proof.json
```

`vuoro doctor --json` emits `vuoro-demo-preflight/v1` and exits 0 when its
prerequisite checks pass, otherwise 1. An explicit `--pg-bin` never falls back
to PostgreSQL on PATH. Both `initdb` and `pg_ctl` must report major version 16.
The check runs only tool version probes and reads installed package metadata;
it starts no database or service and creates no fixture state. A ready report
does not prove settlement, available disk space, or a working live deployment.
Only the demo's receipt records its executed scenario and cleanup outcome.

For a checkoutless run of the **existing source-pinned demo**, use the public
revision below. This historical revision exposes `demo`, not the new `doctor`
command. `uvx` builds the optional consumer from that exact Git revision and
installs its hash-pinned released owner/client dependencies; this is a source
install, not a published `vuoro-demo` release wheel.

```sh
uvx --from 'vuoro-demo @ git+https://github.com/bayleafwalker/vuoro.git@56248feb9c0ce155cc3337ef5f7e90e1c9e4d79b#subdirectory=packages/vuoro-demo' \
  vuoro demo --pg-bin /path/to/postgresql16/bin --receipt proof.json
```

Use a fresh receipt filename. After a successful run, retain `proof.json` as
the bounded evidence and remove the optional environment when finished. No
account is required. Source: [bayleafwalker/vuoro](https://github.com/bayleafwalker/vuoro);
work authority: [bayleafwalker/sprintctl](https://github.com/bayleafwalker/sprintctl).
The unrelated CloudSprints project is not this work authority.

```sh
vuoro demo --pg-bin /path/to/postgresql16/bin --receipt proof.json
```

The command creates its own private PostgreSQL cluster and Unix socket under
`/tmp`, separates migration and runtime roles, verifies runtime DDL refusal,
and starts a loopback HTTP shell. It accepts no database URL, existing backend,
live mode, served profile or cloud credential. Fixture bearer identities use
the shipped evaluation resolver; this proves neither vendor OAuth nor hosted
workers. The demo takes approximately 35 seconds because it waits for natural
owner lease expiry, rather than editing lease timestamps.

The native client creates X and its dependent Y, reserves an exact Release,
creates a real Git commit with its Release trailer, and harvests that observation
through Sprintctl's existing producer and ingest protocol. A proposal admits
the exact revision, reserve key, commit and evidence head. A separate fixture
verifier checks file content, patch applicability and path scope, records a
protected receipt for the raw patch and canonical intent, then accepts that
artifact. Its named checks are scripted conformance assertions, not independent
human review or external execution attestations.

Artifact acceptance leaves X unsettled and Y blocked. The command kills caller
A's real process, proves a fresh B acquisition is refused, waits for the owner
to evaluate A's lease stale, then takes it over. A's late heartbeat and result
are refused; the owner retains its payload. B's named `tests` and `review`
checks settle work via the sole owner Decision writer. The receipt verifies
that Decision's Release and outcome digest and Y's readiness. Raw patch,
canonical intent, verification receipt, Release and outcome digests remain
distinct. Native recovery is demonstrated by reopening and exporting one
incident observation; it does not replay or claim authority.

All successful operation results and expected refusals appear in a private
machine-readable receipt. The service and database stop before success is
reported; fixture data and bearer credentials are deleted. Receipts are created
exclusively with mode 0600. Reusing an output path refuses before startup.
If owned process shutdown cannot be confirmed, the command reports incomplete
and retains private scratch diagnostics instead of deleting a running cluster.
Normal parent SIGTERM and SIGINT cancel the native consumer, clean up owned
worker/service/database processes and record an incomplete receipt. Previous
signal handlers are restored. SIGKILL cannot be handled and is outside this
cleanup guarantee. After a hard kill, identify only this run's private
`/tmp/vuoro-demo-*/owner.json` by its process ID and receipt path, verify the
`postgres/postmaster.pid` belongs to that exact cluster, and stop it with
`pg_ctl -D /tmp/vuoro-demo-EXACT/postgres -m immediate -w stop` before removing
that exact scratch directory. Do not use a broad process or directory cleanup.

```sh
# Both commands must fail honestly and produce status=incomplete receipts.
vuoro demo --pg-bin /path/to/postgresql16/bin --receipt missing-proof.json --omit-required-verification
vuoro demo --pg-bin /path/to/postgresql16/bin --receipt missing-check.json --omit-required-check
vuoro demo --pg-bin /path/to/postgresql16/bin --receipt wrong-artifact.json --wrong-artifact-digest
```

This proves client/process interruption and existing owner settlement on a
disposable database. It does not prove database crash durability, hosted access,
external patch execution or production rollout. Run standalone tests with the
demo installed: `python -m pytest packages/vuoro-demo/tests`.

## Real private handover workflow

`vuoro private-workflow` is an additional local conformance consumer, distinct
from the scripted `demo` and browser walkthrough. Install this package with its
`private` extra for the immutable Sovite owner wheel. Start your own loopback
Ollama service and explicitly install/select an instruction model. The command
never pulls a model, accepts a production database, or enables hosted setup.

```sh
vuoro private-workflow --pg-bin /path/to/postgresql16/bin \
  --model-endpoint http://127.0.0.1:11531 \
  --model qwen3:4b-instruct-2507-q4_K_M \
  --model-digest 0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0 \
  --source /path/to/private-workflow-maintenance.json \
  --state-dir /private/new-workflow-state --receipt /private/new-proof.json
```

Use the public C0 fixture at `docs/fixtures/private-workflow-maintenance.json`.
Both destinations must be new. Private state is retained with mode 0700; the
receipt is created with mode 0600. The operator must retain or remove that state
explicitly. Services stop on completion, failure, SIGTERM and SIGINT; SIGKILL
cannot guarantee cleanup. Never upload private state: it includes local keys,
source/result artifacts and owned database state.

A scoped setup identity proposes a model and agent. Only the owner activates
that draft; the setup identity is then revoked. An initially unqualified task
is refused before inference. After explicit **development-only** authorization,
a producer submits the real task, saves a bounded receipt link in Work evidence,
and is killed. Execution continues in Sovite. A new verifier recovers the saved
link, separately reads source and private output, and appends its observation.

This deliberately narrow profile checks exact M1–M3 source text, three approved
uncertainty/action statements, unassigned owners and no verified outcomes.
Free-form prose is refused rather than classified with keyword heuristics.
These checks qualify only this extraction contract; they do not establish general
reasoning quality, a real maintenance diagnosis, completed repairs or safety.
The owner recovers that observation and rereads the exact private bytes before
authoring its own protected receipt, as required by Sprintctl's accepting identity
binding. Artifact acceptance and the final Work decision remain separate.

Only opaque receipts, configuration/profile identifiers and content hashes enter
Work/Evidence. The proposed Git patch contains a metadata manifest, never raw
source/output, private storage addresses or credentials. The command exercises
refusal of missing source, altered private/patch hashes, incomplete checks, stale
producer access and producer/verifier acceptance. A retried owner decision must
leave exactly one acceptance. This local bearer-identity scenario proves neither
OAuth/agent identity federation, OS-level isolation, hosted qualification, nor a
live vuoro.cloud deployment.

The independent verification observation is labeled
`private-handover-observation/v1`; its referenced manifest remains
`sovite-private-artifact-manifest/v1`. Consumer 0.1.2 corrects an overwritten
observation label from 0.1.1 and records the actual installed harness version.
The [follow-up native proof](../../verification/specialized/private-workflow/schema-fixed.json)
repeats the complete real execution and owner acceptance with those labels.

# Provisional Sovite remote execution client

`vuoro_demo.sovite_execution.SoviteExecutionClient` is an opt-in, standalone candidate adapter for the local Sovite source pinned at `ca9b5df3caef28d6dca0dd587e251aff482dcf11` (`sovite/execution_api.py` and `sovite/remote_execution.py`). It covers `pull`, `start`, `renew`, `status`, `handback`, `complete`, and `lookup` under `/internal/execution/`. It is not connected to `AsyncVuoroClient`, service composition, or a live worker path.

The bearer is supplied directly for the execution-owner API and sent only as an Authorization header to the configured base endpoint. HTTPS is required except for loopback HTTP; redirects are disabled. Requests and streamed response bodies are bounded, response JSON rejects duplicate keys, non-finite values, and excessive depth/size, response models reject unknown fields, and the client does not retry. An ambiguous mutation outcome raises `SoviteExecutionUncertain` with the operation ID and recommends lookup before retrying or advancing. A lookup 404 is unresolved: absence from the journal does not establish that retry is safe.

This is provisional source integration only. Before runtime adoption, qualify all seven route/request/response shapes against the exact pin; bounded streaming and malformed/oversized payload refusals; token scope and redirect behavior; and uncertain-outcome recovery, including the meaning of lookup 404. Update the source pin if the Sovite contract changes.
