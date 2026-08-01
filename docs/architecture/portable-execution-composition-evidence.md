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

The checked-in execution pin selects the ActionQ 0.1.14 service adapter and
its separately released `actionq-contracts` 0.1.1 dependency. Both wheels are
fetched from the same owner release and verified by exact SHA-256 before
installation or import.

`scripts/validate_released_execution_adapter.py` runs in an isolated
environment containing those wheels and the built Vuoro service wheel. It:

- registers the real owner catalog and requires the candidate/group surface;
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
  dist/adapters/actionq_contracts-*.whl \
  dist/adapters/actionq-*.whl \
  'httpx>=0.27,<1'
/tmp/vuoro-released-execution-wheel/bin/python \
  scripts/validate_released_execution_adapter.py \
  packages/vuoro-service/composition/adapter-pins.json dist/adapters
```

No Vuoro candidate/result schema is introduced. Structural runner parity and
integration semantics remain ActionQ evidence; this gate verifies only the
released capability's composition and transport boundary. Image publication,
database migration, identity rollout, and deployment validation remain
separately authorized Appservice work.
