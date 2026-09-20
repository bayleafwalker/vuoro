# Poller deployment (homelab host)

Deploys `vuoro-worker`'s two processes -- the internal MCP server and the
Managed Agents poller -- with the network posture agentops#2469 requires:
**no public listener anywhere in this path.**

## The network posture, concretely

- `internal-mcp` binds `127.0.0.1` (systemd unit) or is reachable only over
  a compose-internal bridge with no `ports:` entry (compose.yaml). Either
  way, nothing outside this host (or this compose project's own network)
  can open a connection to it.
- `poller` opens no listening socket at all. Its only network activity is
  two outbound connections: to `internal-mcp` (loopback or the internal
  compose network) and to Anthropic's Managed Agents queue (outbound HTTPS,
  no inbound path).
- Verifying this after deployment: `ss -tlnp | grep 8765` on the host
  should show `127.0.0.1:8765`, never `0.0.0.0:8765` or a routable address;
  for compose, `docker compose port internal-mcp 8765` should fail (no
  published port exists to query).

## Two ways to run it

**systemd** (`systemd/`): copy both unit files to
`/etc/systemd/system/`, copy `poller.env.example` to
`/etc/vuoro/poller.env` (mode 0600) and fill it in, then:

```bash
systemctl daemon-reload
systemctl enable --now vuoro-internal-mcp.service vuoro-poller.service
```

**compose** (`compose.yaml`): copy `poller.env.example` to `.env` next to
it and fill it in, then `docker compose up -d`. Confirm no port is
published: `docker compose config | grep -A2 ports` should print nothing
for either service.

## What is out of scope here

Registering the Managed Agents agent with Anthropic and provisioning its
credential (`VUORO_MANAGED_AGENTS_*`) are operator, vendor-side steps --
this deployment config assumes they already happened and only wires the
resulting credential in. See `packages/vuoro-worker/README.md`.
