# vuoro-worker

Self-hosted Managed Agents poller wrapping vuoro's internal MCP server
(agentops#2469). This is the strictly-safer half of DECISION 1
(`docs/plans/2026-09-20-vuoro-at-the-edge.md` §§2, 8, 10): a poller process
running on a homelab host you control polls Anthropic's Managed Agents
queue and executes tools locally against an internal, host-local MCP
server. No public endpoint is opened anywhere in this package.

## What it reaches

Managed Agents and the Messages API only. It does not reach Cowork,
claude.ai, mobile, Routines, or anything on the OpenAI side -- MCP tunnels
are not available as claude.ai connectors (edge doc §8, "the option that
avoids all of this"). If interactive-runtime reach is the goal, that is
E1-E4's public surface, not this package.

## Components

- `internal_tools.py` -- the eight-tool surface (read/coordinate/record/
  propose), wired directly to E0's `LeaseStore`, `RateLimiter`,
  `RequestMetrics`, `EvidenceSetBuilder` and `StaticBearerIdentityResolver`
  (agentops#2464). No new lease, rate-limit, metrics or identity logic --
  this module is the wiring, not a second implementation.
- `mcp_server.py` -- an internal-only MCP-over-HTTP server fronting
  `internal_tools`. Supports both the legacy `initialize` handshake and the
  2026-07-28 sessionless era (SEP-2567). Bind it to loopback or an internal
  network only; see `deploy/poller/README.md`.
- `mcp_client.py` / `custom_tools.py` -- wrap the internal MCP server's
  tools as Managed Agents custom tools.
- `poller.py` -- the poll/dispatch loop: preflight-checks tool
  availability before every dispatch, and is idempotent per task id.
- `managed_agents.py` -- the real Managed Agents queue client. **Not
  exercised against the vendor in this repository's tests**: it needs an
  operator-registered agent, which is outside this package's writable
  scope. `poller.FakeQueueClient` stands in for it in tests.

## Running

```bash
export VUORO_POLLER_TOKEN=...        # static bearer this host accepts
uv run --package vuoro-worker vuoro-worker serve-internal-mcp   # loopback only, by default

export VUORO_MANAGED_AGENTS_BASE_URL=...
export VUORO_MANAGED_AGENTS_TOKEN=...
export VUORO_MANAGED_AGENTS_AGENT_ID=...
uv run --package vuoro-worker vuoro-worker poll
```

Registering the Managed Agents agent itself, and provisioning its
credential, are operator, vendor-side steps this package does not perform.
