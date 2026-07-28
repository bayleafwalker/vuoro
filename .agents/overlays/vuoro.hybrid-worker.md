# Vuoro hybrid-worker overlay

Applies when a bounded OpenCode worker implements a frozen `agentops-task/v1`
packet against this repository. The governing contract is
`/projects/dev/agentops/templates/dispatch/hybrid/hybrid-dispatch.v1.json`.

## Worker-eligible work

- Tests, fixtures, and parametrization under `packages/*/tests/` and `tests/`.
- Mechanical refactors inside `packages/vuoro-service/src/` whose interface and
  acceptance contract the coordinator already fixed in the packet.
- Client polling, long-poll, reconnect, and fixture mechanics when the packet
  freezes the public interface, schemas, and acceptance.
- Documentation under `docs/` that restates an already-decided contract.

## Never worker-eligible

- Client authority, compatibility, lifecycle, catalog, or public-contract
  decisions. Any database, migration, adapter, authority, or domain-core
  import is a release-blocking architecture violation.
- `deploy/**` — neutral public packaging; appservice-specific identities,
  addresses, credentials, and rollout policy do not belong here at all.
- Compatibility gates, schema/migration assets, and runtime-vs-migration role
  separation. Startup must never migrate automatically, and a worker must not
  be the thing that decides it does.
- Catalog derivation, `pyproject.toml`, `uv.lock`, and anything that changes
  which adapter releases compose into the service artifact.

## Stop conditions

Return a structured blocker instead of guessing when the packet would require:

- adding a dependency, changing a lockfile, or reaching the network;
- touching a path outside `writable_patch_paths`;
- resolving an ambiguity in the compatibility, authority, or migration
  semantics rather than implementing a decided one;
- choosing client authority, lifecycle, catalog, or public semantics not
  frozen by the packet;
- weakening, skipping, or deleting an existing test to make a gate pass.

A worker never runs `git`, never touches sprintctl state, and never contacts a
deployed Vuoro endpoint. Verification runs only the packet's registered command
ids against repository fixtures.

This bounded patch worker is not an Actionq portable runner. It returns a
workspace diff to the coordinator and therefore must not claim conformance with
the `ExecutionEnvelope`, candidate Git bundle, receipt, settlement, or
integration contracts in `docs/architecture/portable-execution.md`. Future
portable runners remain separate implementations; no worker may change the
policy that admits or constrains them.
