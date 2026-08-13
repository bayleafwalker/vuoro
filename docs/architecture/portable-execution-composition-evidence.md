# Portable execution composition evidence

Status: source composition verified for Vuoro #2037. Deployment is not
authorized by this record.

Vuoro consumes the owner-released execution surface without compiling plans,
claiming work, running a harness, publishing Git objects, or interpreting
ActionQ lifecycle state.

## Frozen owner evidence

- ActionQ #2033 released the devbox/OCI parity proof in
  [ActionQ v0.1.12](https://github.com/bayleafwalker/actionq/releases/tag/v0.1.12).
  The owner gate ran the same `execution-envelope/v1` contract through both
  implementations, validated candidate/result/receipt structure, and passed
  324 tests with 7 environment-gated skips.
- ActionQ #2035 released immutable candidate verification, integration, and
  review actions in
  [ActionQ v0.1.14](https://github.com/bayleafwalker/actionq/releases/tag/v0.1.14)
  at `dd41a9860cf9f07a4776f8279e048d70fd6dbb05`. Its disposable PostgreSQL gate
  passed 371 tests with 10 environment-gated skips.
- AgentOps #2036 merged the deterministic Sprintctl-to-envelope compiler in
  [AgentOps PR #12](https://github.com/bayleafwalker/agentops/pull/12). Vuoro
  consumes its content-addressed plans; it does not reproduce the compiler.

## Vuoro composition proof

The checked-in execution descriptor selects `actionq-schema/v11` through
ActionQ 0.1.21 at
`8ef1fc9ae58b96ddc90db0e5be7a323e9be4b85b`. Its official release wheel is
[`actionq-0.1.21-py3-none-any.whl`](https://github.com/bayleafwalker/actionq/releases/download/v0.1.21/actionq-0.1.21-py3-none-any.whl)
with SHA-256
`7f4c3cbbbe991465ac88fa0640f1bb4420f76c8a07a9e7765e61a0981631220d`.
That wheel declares the exact `actionq-contracts==0.1.1` dependency, which
remains a separately locked official owner release at its own source revision,
and the shared `vuoro-adapter-kit` 0.1.0 lock is reused by all consumers. Each
wheel is fetched and verified by its exact SHA-256 before installation or
import; the dependency locks are not rewritten to imply they were published by
the ActionQ 0.1.21 tag.

`scripts/validate_released_execution_adapter.py` runs in an isolated
environment containing those wheels and the built Vuoro service wheel. It:

- registers the real owner catalog and requires all 26 operations, including
  the candidate/group surface;
- verifies the frozen owner catalog metadata hash
  `8d434e8b347e804c90e48a6598304be84b12f2a61ebc2dbed00a26053239a778`;
- verifies the four additive completion operations and preserves byte equality
  for the old 22-operation catalog subset
  (`1b25af2143d4a8895fba83954d69c420e3ff0a364f6fc94269d39d7cac2ed8e3`);
- verifies owner compatibility at schema 11 and packaged migration
  `011_session_completion_log.sql`;
- invokes immutable-candidate creation and group realization through Vuoro's
  authenticated invocation shell;
- proves actor and repository provenance reach the owner adapter unchanged;
- proves missing authority and caller-spoofed fields fail before an owner
  callback; and
- rejects any migration or runner operation in the served catalog.

The source gate is:

```bash
python scripts/fetch_pinned_adapters.py \
  packages/vuoro-service/composition/adapter-pins.json dist/adapters
uv build --package vuoro-service --wheel --out-dir dist/vuoro-service
uv venv --python 3.12 /tmp/vuoro-released-execution-wheel
uv pip install --python /tmp/vuoro-released-execution-wheel/bin/python \
  dist/vuoro-service/*.whl \
  'httpx>=0.27,<1'
uv pip install --python /tmp/vuoro-released-execution-wheel/bin/python \
  --no-deps \
  dist/adapters/actionq_contracts-*.whl \
  dist/adapters/actionq-*.whl
uv pip install --python /tmp/vuoro-released-execution-wheel/bin/python \
  --no-deps dist/adapters/vuoro_adapter_kit-*.whl
uv pip check --python /tmp/vuoro-released-execution-wheel/bin/python
/tmp/vuoro-released-execution-wheel/bin/python \
  scripts/validate_released_execution_adapter.py \
  packages/vuoro-service/composition/adapter-pins.json dist/adapters
```

CI also installs all four pinned owner wheels in one disposable environment
and runs `scripts/validate_released_catalog_composition.py`; that gate asserts
84 operations, the four-domain counts (43 work, 26 execution, 10 knowledge,
5 audit), and the accepted revision
`fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196` without
opening a database.

No Vuoro candidate/result schema is introduced. Structural runner parity and
integration semantics remain ActionQ evidence; this gate verifies only the
released capability's composition and transport boundary. Image publication,
database migration, identity rollout, and deployment validation remain
separately authorized Appservice work.

## Four-domain catalog and deployment prerequisite

With ActionQ 0.1.21's 26 operations, the accepted four-domain service catalog
contains 84 operations and has revision
`fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196`. The
completion operations are additive; the previous 22 execution operation
descriptors remain byte-identical and stale catalog revisions continue to be
rejected by the existing `stale-catalog` envelope path.

Schema 11 is not enabled by this source promotion. Before any service image
can serve completion operations, Appservice must separately coordinate:

- a migration Job using the ActionQ migration identity to apply migration 011;
- a runtime execution DSN/secret and role for queue lifecycle operations;
- a distinct completion-ingest DSN/secret and
  `VUORO_EXECUTION_COMPLETION_INGEST_DSN`,
  `ACTIONQ_COMPLETION_INGEST_ROLE`, with authority
  `execution.session-completion.ingest`;
- a distinct completion-read DSN/secret and
  `VUORO_EXECUTION_COMPLETION_READ_DSN`,
  `ACTIONQ_COMPLETION_READ_ROLE`, with authority
  `execution.session-completion.read`; and
- privilege verification that ingest has only completion projection append
  rights, read has only completion projection SELECT rights, and neither
  completion role can mutate queue actions/events, create schema objects, or
  write the migration ledger.

The current appservice/runtime configuration does not provide these completion
roles, DSNs, secrets, or authority bindings. This PR records the prerequisite
only; it does not enable completion traffic, run migration 011, publish an
image, or deploy.

Vuoro rejects missing, empty, runtime-equal, or ingest/read-equal completion
DSNs before constructing or registering the ActionQ application. The explicit
factories are passed to `ActionQApplication`; its fallback to the execution
runtime connection is therefore unreachable for schema-v11 completion
handlers. Local-only fixtures must provide three explicit disposable DSNs.
