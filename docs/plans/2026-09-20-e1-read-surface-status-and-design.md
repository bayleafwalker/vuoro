# E1 read-only public MCP surface — status and design (agentops#2465)

> **Superseded 2026-09-23.** `mcp_surface.py` and its static bearer are gone.
> The protocol server now lives in `packages/vuoro-mcp-edge` behind gateway
> identity assertions and runs as `vuoro-service mcp-serve`; see that
> package's README.

## What landed

`packages/vuoro-service/src/vuoro_service/mcp_surface.py`: a standalone,
self-contained MCP protocol shell exposing exactly the two tools agentops#2465
scopes -- `list_ready_work` and `describe_work`, both read-bucket per the
edge doc's boundary (`docs/plans/2026-09-20-vuoro-at-the-edge.md` section 3).
No claim, no record, no propose tool exists in this module, and no
`vuoro:effect.apply` scope is defined anywhere in it (TS-16).

The module does not import, and is not imported by, `app.py` or
`composition.py` -- the existing protocol-v1 shell those files build is
untouched. This is deliberate: it is what makes "deleting the connector
fully removes public reachability with no other code change required"
(the item's third acceptance line) true at the source level, not only at
the deploy level. Nothing elsewhere in the package composes or references
`mcp_surface.py`; not wiring it in is itself part of the reversibility.

Tests: `packages/vuoro-service/tests/test_mcp_surface.py`, 25 cases. Every
guard added (405 on GET/DELETE, Origin allowlist, bearer+scope
requirement, header/body agreement check) was verified to actually fail
closed by disabling it, capturing the failure, and re-enabling it -- see
the worker's dispatch-note evidence; a reasoned account was not treated as
sufficient per the operator's silent-pass rule.

### Compliance essentials implemented (edge doc section 4)

- One HTTPS POST endpoint (`/mcp`); explicit `405` handlers on GET and
  DELETE at the same path (not relied on as a Starlette routing default).
- `Origin` validation: an allowlist (`allowed_origins`, empty by default).
  A request with no `Origin` header is accepted (normal for server-to-server
  MCP clients); a request with an `Origin` header not in the allowlist is
  rejected with `403` and JSON-RPC error `-32002`.
- Header-to-body agreement checks on `MCP-Protocol-Version`, `Mcp-Method`
  and `Mcp-Name`: when a header **and** its body counterpart are both
  present and disagree, the request is rejected with JSON-RPC error
  `-32020`. Absence on either side is not a mismatch -- a dual-era client
  legitimately omits these headers.
- `server/discover` implemented, callable with no prior `initialize`.
- `resultType: "complete"` on every result. (Until 2026-09-26 the edge sent
  per-method names -- `tools-list-result` and so on -- which the 2026-07-28
  revision does not allow: Claude Code refused the tool list and hosted
  Routines saw no Vuoro tools.)
- `ttlMs` and `cacheScope` on every list and read result (both tools).
- Deterministic tool ordering: `TOOL_ORDER = ("list_ready_work",
  "describe_work")`, a fixed tuple, never a dict-iteration or a sort.
- Stateless handlers throughout: no session object, no server-minted
  handle -- neither tool needs one, since both are pure reads.

### Dual-era protocol support -- flagged unverified, as instructed

The item's source (the edge doc) claims Anthropic clients were still
opening sessions with `initialize` as of mid-2026, while `2026-07-28` is
described as the current, sessionless revision. This module supports both
paths in the same handler: `initialize` / `notifications/initialized` for
legacy clients, and direct `server/discover` / `tools/call` with no
handshake for clients on the newer revision. **The specific claim that
Anthropic clients still open with `initialize`, and the exact set of legacy
protocol-version strings (`LEGACY_PROTOCOL_VERSIONS` in
`mcp_surface.py`) are carried over from the edge doc as a third-party,
dated claim and are not independently verified by this implementation.**
Re-check the live client matrix before ever dropping `initialize` support;
until then, the surface accepts either era and negotiates whichever
protocol version the caller offers, from `SUPPORTED_PROTOCOL_VERSIONS`.

### Auth (agentops#2470, decided)

`BearerGrant` maps an opaque static token to a `frozenset` of scopes,
shaped as if it were an OAuth scope grant per the edge doc section 5
("design the schema as if it were OAuth"). Exactly one scope exists today,
`vuoro:work.read`; both tools require it. This reuses the *shape* of the
existing `StaticBearerIdentityResolver` in `identity.py` without reusing
that class directly, because this surface's identity concept (a scope set,
not a repo-scoped `Identity`) is narrower and does not need the
protocol-v1 envelope's repo/authority/idempotency fields.

Rate limiting and request metrics are optionally injected
(`rate_limiter`, `metrics` parameters to `create_mcp_app`), reusing the
already-hardened `RateLimiter` (`rate_limit.py`) and `RequestMetrics`
(`metrics.py`) from E0 (agentops#2464) rather than building new ones.

### Data source: intentionally adapter-shaped, not wired to a real backend

vuoro-service does not own domain state (`AGENTS.md`: "Domain state
machines and migration assets remain in their owner repositories"). No
`WorkRelease` concept existed anywhere in this repository before this item
(checked: `grep -rn "ready_work\|WorkRelease" packages` was empty). This
item's Writable line is "vuoro MCP surface source ... connector
registration config/docs" -- it does not authorize inventing or wiring a
production work-item backend, and sprintctl (the actual system of record
for work items) is explicitly out of vuoro's ownership per `AGENTS.md`
("Sprintctl ... retain[s] ... work ... semantics").

So `mcp_surface.py` defines a `ReadyWorkSource` Protocol
(`list_ready_work`, `describe_work`) and a safe default, `_EmptyWorkSource`,
that returns no results rather than failing to start. Composing a real
adapter -- one that calls sprintctl's served backend, or another system of
record -- is follow-up work, not done here, and is the first thing a
"does this surface actually get used" evaluation needs once it is
reachable at all. Until an adapter is wired, the surface is honest and
inert: `list_ready_work` returns an empty list, `describe_work` returns
"not found" for every id.

## What remains before this is a working E1 (in order)

1. **Wire a real `ReadyWorkSource` adapter.** Not attempted here (see
   above) -- needs a decision about which system of record E1 queries and
   how, which is exactly the kind of adapter-composition decision
   `AGENTS.md`'s hybrid-dispatch table marks coordinator-only.
2. **Compose and run the MCP app somewhere reachable inside the cluster.**
   `create_mcp_app()` returns a plain ASGI app; it is not yet mounted
   behind vuoro-shared's existing internal path, and no cluster manifest
   in `appservice` references it. That wiring lives in a different repo
   (`appservice`) and is out of this item's Writable scope.
3. **Only then** does exposing it publicly (connector registration,
   public HTTPS reachability) become the operator's decision to execute --
   see the runnable operator block in the item report.

## The one-month stop condition (carried into the item record)

Per agentops#2465's acceptance: if E1 runs for a month with no case of the
substrate being reached from a hosted runtime, E2-E4 are not built, and the
read surface is deleted rather than kept warm. This is recorded as a
tracker note on agentops#2465 in addition to here; both should agree.
