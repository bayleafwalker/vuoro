# vuoro-mcp-edge

The vuoro MCP protocol server. It serves two read tools to hosted runtimes
(claude.ai, Cowork, Routines, cloud sessions) behind the vuoro.cloud gateway,
and reads the sprintctl public-work contract through the runtime shell on
localhost.

Run it from the service image:

```
vuoro-service mcp-serve --port 8081 [--host 0.0.0.0]
```

`mcp-serve` needs this package installed beside `vuoro-service` (the image
installs both). `vuoro-service` does not depend on this package: this package
depends on `vuoro-service`, so the CLI names the app factory as a string for
uvicorn and never imports it itself.

## Request path

```
client --OAuth--> vuoro.cloud gateway --X-Vuoro-Identity + X-Request-Id--> POST /mcp (this server, :8081)
                                                                  |
                               same headers, forwarded verbatim   v
                                            POST http://127.0.0.1:8080/api/invoke/v1 (runtime shell)
                                                                  |
                                                                  v
                                                sprintctl work.public.list-v1 / item-v1
```

- **Inbound auth is the gateway assertion and nothing else.** The Ed25519 JWT
  in `X-Vuoro-Identity` is verified with the runtime shell's own verifier and
  trust configuration: issuer, audience, key id, expiry, workspace,
  environment, repository binding and request-id correlation. Its
  authorities must be exactly what the scope table uses (today only
  `work:read`); a broader assertion is refused, not narrowed. A missing or
  invalid assertion gets HTTP 401 with JSON-RPC error `-32001`. The gateway
  owns the OAuth challenge, so this server sends no `WWW-Authenticate`.
- **No credential in this process.** No workspace token, no DSN, no signing
  key. The server refuses to start if any environment variable ends in `_DSN`
  or holds a `vuo_pat_` token.
- **Upstream** is the shell's invoke API with the same assertion and request
  id, `X-Vuoro-Client-Protocol: 1`, the invocation `request_id` set to that
  request id, and `repo_id` set to the single repository in the assertion
  (zero is `workspace-unbound` and more than one is `workspace-ambiguous`,
  both tool errors). The catalog is checked once per process and refetched
  once on `stale-catalog`. Results are never cached, because authorization is
  per caller.

## Tools

| tool | scope | authority | operation | returns |
|---|---|---|---|---|
| `list_ready_work` `{limit?: 1..50 = 50}` | read | `work:read` | `work.public.list-v1` | items with `status == "pending"` and `blocked == false` (sprintctl's ready rule), owner order, each exactly `work_id, title, priority, status, blocked, updated_at` |
| `describe_work` `{work_id: integer >= 1}` | read | `work:read` | `work.public.item-v1` | the item whose `work_id` matches the request (a different one is `upstream-mismatch`), in any status, exactly the six list fields plus `created_at, resolution, blocked_by` |

The scope table (`TOOL_SCOPES`, `SCOPE_AUTHORITIES` in `server.py`) is the
list of what can be called. A tool without a row is neither listed nor
callable. Adding a tool (E2) means adding a row, a definition and a handler.

**Strict emission.** Every upstream record's key set must equal the
contract's field set exactly. An extra key (for example a leaked
`description`) or a missing one fails the call with `contract-violation`, and
the record does not reach the client. Values are checked too: priority 1..9
or null, the four-value status (a `done` record in the list is a contract
violation), title <= 160 and other strings <= 256 characters. Rejections (`postgres-runtime-unavailable`,
`item-not-found`, `authority-required`, ...), a non-`ok` state, transport
failures and catalog mismatches are all tool errors (`isError: true`,
`structuredContent.error.{code,message}`). None of them is ever reported as an
empty list, so an empty `items` means there is no ready work. Upstream error
text never reaches the client: known codes get messages written in this
package, and any other code becomes `upstream-rejected` with only the code
(<= 64 characters) in `error.upstream_code`. Bad arguments and unknown tools
are tool errors too (`invalid-params`, `unknown-tool`).

## Protocol

One stateless endpoint, `POST /mcp`, answering plain JSON. JSON-RPC errors
(`-32700`, `-32600` for a missing/wrong `jsonrpc` or a batch, `-32601`, `-32020`)
are HTTP 200 with an error body; `ping` returns `{}`. A request without an `id`
is a notification and a body with no `method` is a client response: both get
202 with no body. HTTP status is used only for 401 (assertion), 400
(unsupported `MCP-Protocol-Version` header), 413 (body over 64 KiB), 415
(Content-Type not JSON) and 405 (`GET`, `DELETE`). There is no SSE and no
`Mcp-Session-Id`. Tools carry a `title` and read-only `annotations`. Both
protocol eras work: `initialize` (echoing 2024-11-05, 2025-03-26, 2025-06-18
or 2025-11-25, otherwise
2026-07-28) followed by `notifications/initialized` (202), or `server/discover`
and `tools/call` with no handshake per the 2026-07-28 revision. Mismatches
between `MCP-Protocol-Version`, `Mcp-Method` or `Mcp-Name` and the body are
`-32020`. Every result carries `resultType`, `ttlMs` and `cacheScope`.
`GET /health/live` is an unauthenticated liveness probe.

## Environment

The gateway trust variables are the runtime shell's own, read the same way
(`vuoro_service.composition.load_gateway_assertion_resolver`), so both
processes trust exactly the same things.

| variable | required | meaning |
|---|---|---|
| `VUORO_ENVIRONMENT_NAME` | yes | deployment environment; must match the hosted project binding |
| `VUORO_WORKSPACE_ID` | yes | the workspace ULID the assertion must carry |
| `VUORO_GATEWAY_PUBLIC_KEY_FILE` | yes | the gateway's Ed25519 public key, at the approved mount `/etc/vuoro/identity/gateway-public.pem` |
| `VUORO_GATEWAY_ASSERTION_ISSUER` | yes | expected `iss` |
| `VUORO_GATEWAY_ASSERTION_AUDIENCE` | no, default `vuoro-service` | expected `aud` |
| `VUORO_GATEWAY_ASSERTION_KEY_ID` | no, default `gateway-2026-01` | expected `kid` |
| `VUORO_PROJECT_BINDINGS_FILE` | yes in Cloud | must name the approved mount `/etc/vuoro/bindings/bindings.json`; the image's embedded default is not a hosted binding and is refused |
| `VUORO_MCP_UPSTREAM_URL` | no, default `http://127.0.0.1:8080` | runtime shell base URL |
| `VUORO_MCP_UPSTREAM_TIMEOUT_SECONDS` | no, default `5` | upstream request timeout, in (0, 30] |

## Tests

```
uv run --package vuoro-mcp-edge --extra test pytest packages/vuoro-mcp-edge/tests
```

`test_edge_end_to_end.py` runs the edge against the unmodified runtime shell
(`vuoro_service.app.create_app`) over ASGI, so the forwarded assertion goes
through the shell's own verification.
