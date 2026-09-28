# Changelog

All notable changes to the separately versioned Vuoro distributions will be
recorded here.

## Unreleased

- vuoro-mcp-edge (unreleased): the coordinate bucket ships (agentops#2520, E2b).
  `claim_tools.build_toolset` now serves `claim_work`, `heartbeat` and
  `report_outcome` (bucket `coordinate`, authority `work:claim`) over
  sprintctl 0.10.0's durable lease: `work.lease.acquire-v1`,
  `work.lease.heartbeat-v1` and `work.lease.report-outcome-v1` (never the
  deprecated `work.lease.complete-v1` alias). The outcome tool is
  `report_outcome`, not `complete_work`, per the contract's agentops#2540
  amendment. Each tool resolves the caller's run first, then makes exactly one
  owner call through the record store's shell client
  (`SprintctlRecordStore.shell_client`, new) with the caller's assertion; the
  edge holds no lease state and never schedules, expires or retries.
  `claim_work` refuses `ttl_seconds` (`invalid-arguments`), and a same-key
  retry is re-evaluated by the owner (`resumed: true`), not replayed.
  `heartbeat` takes no `idempotency_key`. A lease without
  `heartbeat_interval_seconds`, or an adapter without the operations, is
  `claim-owner-incompatible`. Owner refusal codes and messages pass through
  unchanged (`claim-superseded` carries its generations in the message only,
  because vuoro-service's `OperationRejectedError` has no details). The tools
  are listed wherever the durable record store is composed; they stay
  uncallable through the gateway until vuoro-cloud grants `vuoro:work.claim`
  and adds their `MCP_TOOL_SCOPES` rows. The edge and the tenant runtime must
  be pinned together: these tools need a runtime whose work adapter is
  sprintctl 0.10.0 or later. `tools/list` and `server/discover` now list only
  the tools whose bucket authority the caller's assertion carries (built-in
  read tools and every toolset alike), and `tools/call` checks that authority
  before it validates arguments. The version bump to 0.1.6 is left to the
  release PR.

- vuoro-service 0.1.78 / vuoro-mcp-edge 0.1.5: the work adapter is pinned to
  sprintctl 0.10.0
  (56bfbc4, wheel sha256 `d8cc3515…4270`; remote schema stays 18, so no
  tenant migration). The 0.10.0 adapter catalog makes lease verification
  profiles requirement sets (`verification.profile` enum -> pattern,
  `requirements` required, agentops#2539) and adopts the operator's lease
  contract (600 s authority TTL, the `ttl_seconds` acquire input removed,
  120 s heartbeat, derived generation, `claim-superseded`, agentops#2540).
  It adds `work.lease.report-outcome-v1` and keeps `work.lease.complete-v1`
  as a deprecated alias (work operations 62 -> 63). No Vuoro code calls
  the sprintctl lease operations yet: the claim toolset stays unserved and
  `vuoro:work.claim` is still reserved. The released-adapter validators
  expect the 0.10.0 catalog. (agentops#2539, agentops#2540, #148)

- vuoro-service 0.1.78: `lease.py`'s in-memory `LeaseStore` conforms to INV-L1
  (the operator's lease contract, agentops#2540). A late completion under an
  expired or superseded lease is still refused with `LeaseNotCurrentError`,
  but when it comes from the lease's own holder, it and its optional
  `result` are now retained as a non-settling outcome
  (`RetainedOutcome`, `disposition="stale"`, `settlement_effect="none"`,
  read through `LeaseStore.retained_outcomes(subject)`) instead of being
  discarded. `complete` takes an optional keyword `result`. An unknown lease
  id, another holder's completion, or a retry of a completion that already
  succeeded still leaves nothing, and the refusal looks the same either
  way. The retained result is a copy. The E2/E3 contract
  §6 records the sprintctl lease changes from agentops#2539 and #2540.
  (agentops#2540, #147)

- vuoro-mcp-edge 0.1.5: `/mcp` conforms to the published MCP 2026-07-28 schema
  where the `mcp-strict-client` job found it did not (agentops#2526). Wire
  changes: `ping` answers `{"resultType": "complete"}` (was `{}`); an error
  answered before the request id is known (parse error, batch, non-object
  body, invalid id, and the 401/400/413/415/503/405 refusals) omits `id`
  instead of sending `"id": null`; a header/body mismatch (`-32020`) is
  HTTP 400 (was 200); an unsupported `MCP-Protocol-Version` is `-32022` with
  `data.requested` and `data.supported` (was `-32600` without data), still
  HTTP 400. The strict-client job's known-deviation list is now empty and it
  checks assertion replay refusal and upstream edge proofs (#134).
  (agentops#2526, #146)

- vuoro-mcp-edge 0.1.5 (breaking for `RunRegistry` implementers): run continuation
  across identities (TS-8 second route). `register` takes an optional
  `predecessor_run_id`. The registry refuses it with
  `predecessor-not-eligible` unless the predecessor shares the caller's
  workspace and repository. The edge first requires `work:read`. The new
  read-bucket tool `read_predecessor_context` returns the predecessor's
  session notes and evidence through the successor's own run. The successor
  inherits no authority: the predecessor's run still resolves only to its own
  binding. Neither is advertised against `SprintctlRecordStore` until sprintctl
  records predecessors, so the served tool list is unchanged. The E2/E3
  contract §4 records the rules. (agentops#2525, #145)

- vuoro-service 0.1.78 / vuoro-mcp-edge 0.1.5: replay hardening from the #134 review (agentops#2530). The
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
  carries the repo. `IdempotencyLedger` is unchanged. (agentops#2530, #144)

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
