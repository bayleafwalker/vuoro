# Changelog

All notable changes to the separately versioned Vuoro distributions will be
recorded here.

## Unreleased

- vuoro-service: replay hardening from the #134 review (agentops#2530). The
  shell's startup watermark now applies to the edge-proof route as well as
  the direct route, so a shell-only restart no longer resets the per-jti
  proofed-use cap (7): an assertion issued more than 2 s before the shell
  started is refused with 401 `identity-replayed` (edge: `-32003`) on
  either route. A tool call whose shell calls span a shell restart now fails
  as a tool error carrying `identity-replayed` (the edge does not turn it
  into `-32003`), and the client calls the tool again. The assertion lifetime bound
  (`exp - iat` at most 30 s, which the 50,000-entry replay cache is sized
  for) is a named constant and is tested to refuse long-lived assertions
  before they reach the cache. vuoro-mcp-edge tests: the ledger behaviour
  test compares winners by value, says its gathered writers are not a race
  for the in-memory ledgers, and records that sprintctl's ledger key also
  carries the repo. `IdempotencyLedger` is unchanged. (agentops#2530)

- vuoro-service 0.1.77 / vuoro-mcp-edge 0.1.4: the work adapter is pinned to
  sprintctl 0.9.0
  (711ccb2, wheel sha256 `9ad68e09…2e7a`; remote schema 18, adding the
  exclusive durable work lease and outcome reports, agentops#2520). The
  0.9.0 adapter refuses a schema-17 tenant until that tenant's
  `vuoro-migrate` job has run the 17 -> 18 migration on roll-out. The adapter
  registers four new `work.lease.*-v1` operations. The claim toolset stays
  unserved: `vuoro:work.claim` is still reserved and `claim_tools` still
  lists nothing. (agentops#2520, #142)

- vuoro-service 0.1.77 / vuoro-mcp-edge 0.1.4: gateway assertions are accepted
  once (agentops#2519). The first verifier (the shell for direct gateway
  traffic, the MCP edge for `/mcp`) consumes `(subject, jti)` in a bounded,
  expiring in-memory cache; a replay is 401 `identity-replayed` (edge:
  JSON-RPC `-32003`), a full cache refuses new assertions with 503
  `identity-replay-capacity` (edge: `-32004`). `jti` no longer has to equal
  `request_id`, and its format is not checked. Edge -> shell calls carry a
  one-use, body-bound `X-Vuoro-Edge-Proof` under a pod-local key, so
  multi-call tools keep working and a captured call cannot be replayed. The
  shell records which route claimed each jti and refuses it on the other,
  and accepts at most 7 proofed uses per assertion. While `/mcp` and the
  shell share an audience, the edge requires `client_id` and `grant_id`.
  Deployment: both containers need a
  memory-backed `emptyDir` at `/run/vuoro/edge-proof` and
  `VUORO_EDGE_PROOF_KEY_FILE=/run/vuoro/edge-proof/key` (the edge refuses to
  start without it). `VUORO_EDGE_GATEWAY_ASSERTION_AUDIENCE` is new and
  optional. See `docs/architecture/gateway-identity.md`. (agentops#2519, #134)

- vuoro-mcp-edge 0.1.4 (breaking for ledger implementers): the shared
  `IdempotencyLedger` protocol takes `(workspace_id, principal_id, tool, key)`;
  `InMemoryIdempotencyLedger` and the effect intent store use it directly (the
  intent store no longer folds the principal into the workspace argument), and
  one behaviour test covers every ledger. The E2/E3 contract records the claim
  lease owner's decisions (sprintctl 0.9.0 `work.lease.*-v1`); the claim
  toolset stays unserved until every tenant runtime serves it and
  `vuoro:work.claim` is granted. (agentops#2520, #141)

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
