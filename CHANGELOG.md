# Changelog

All notable changes to the separately versioned Vuoro distributions will be
recorded here.

## Unreleased

- vuoro-service 0.1.76 / vuoro-mcp-edge 0.1.3: the work adapter is pinned to
  sprintctl 0.8.0 (remote schema 17; its migration runs in each tenant's
  `vuoro-migrate` job on roll-out). The edge serves E2's record bucket
  (`register_run`, `append_evidence`, `write_session_note`; agentops#2466)
  over the durable `SprintctlRecordStore`. They are listed, but not callable
  through the hosted gateway until vuoro-cloud grants `vuoro:evidence.record`
  and adds these tools to `MCP_TOOL_SCOPES` (until then the gateway answers
  403 `insufficient_scope`); a call reaching the edge without `work:evidence`
  answers `authority-required`. E3's propose bucket (`propose_effect`, `get_effect`;
  agentops#2467) lists nothing until a durable intent store exists; its
  trusted-side reconciler, `packages/vuoro-reconciler` 0.1.0, is not part of
  the service image. The shared toolset seam and E2/E3 contract (#129) are
  amended: `RunRegistry` takes the forwarded identity, and idempotency is
  keyed by (workspace, principal, tool, key). The image now installs the
  workspace package `vuoro-evidence` from the repository instead of
  resolving it from an index. (#129, #131, #130, #132)

- vuoro-mcp-edge (breaking for ledger implementers): the shared
  `IdempotencyLedger` protocol takes `(workspace_id, principal_id, tool, key)`; `InMemoryIdempotencyLedger` and
  the effect intent store use it directly (the intent store no longer folds
  the principal into the workspace argument), and one behaviour test covers
  every ledger. The E2/E3 contract records the claim lease owner's decisions
  (sprintctl 0.9.0 `work.lease.*-v1`); the claim toolset itself follows
  vuoro#134. (agentops#2520)

- vuoro-client 0.1.1: authenticate `GET /api/meta/v1/handshake` and
  `GET /api/catalog/v1` whenever the profile has a credential (hosted
  vuoro.cloud answered 401 to served sprintctl), and send a named
  `User-Agent: vuoro-client/<version>` on every request (Cloudflare refuses
  generic ones). (#125)
- vuoro-service 0.1.75 / vuoro-mcp-edge 0.1.2: `describe_work` for a missing
  item is an ordinary `item-not-found` tool error logged at INFO, no longer a
  "work source failed" warning; real unavailability is unchanged. (#126)
- vuoro-worker: the internal MCP server answers with 2026-07-28
  `resultType: "complete"` (the domain kind moves to `kind`) and
  `cacheScope` public/private. (#127)

- vuoro-service 0.1.74 / vuoro-mcp-edge 0.1.1: MCP results carry
  `resultType: "complete"` and list results `cacheScope: "private"`, as
  revision 2026-07-28 requires. The per-method names before this made Claude
  Code and hosted Routines drop every Vuoro tool.

- Add `vuoro-service mcp-serve` and restore `packages/vuoro-mcp-edge`: the MCP
  protocol server (`list_ready_work`, `describe_work`) over gateway identity
  assertions, reading sprintctl's `work.public.*-v1` contract through the
  runtime shell. Remove the static-bearer `vuoro_service.mcp_surface` module.

- Bootstrap the public repository and independent `vuoro-client` and
  `vuoro-service` package boundaries.
- Add protocol-v1 handshake, ETag catalog, safe JSON Schema registration,
  identity-derived generic invocation, and dynamic operation discovery.
