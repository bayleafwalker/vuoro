# Gateway identity assertion contract

Vuoro Cloud owns external tokens, workspace membership, gateway routing, and
Ed25519 signing. Vuoro owns verification at the tenant runtime boundary and
continues to apply its catalog authority and repository checks. The runtime
does not query Cloud state or become the workspace authority.

The hosted mode is enabled only when the deployment supplies all of these
inputs:

- `VUORO_GATEWAY_PUBLIC_KEY_FILE`, exactly
  `/etc/vuoro/identity/gateway-public.pem`, as a read-only Cloud-owned mount;
- `VUORO_WORKSPACE_ID`, the immutable workspace ULID commissioned with the
  mounted one-project binding;
- `VUORO_ENVIRONMENT_NAME`, which must exactly match the binding's
  `environment` field.
- `VUORO_GATEWAY_ASSERTION_ISSUER`, which must exactly equal Vuoro Cloud's
  configured `Settings.environment_id` (the JWT `iss` value). Vuoro does not
  guess or default this authority-bearing value.

The audience and key-id settings default to Cloud's current contract:
`vuoro-service` and `gateway-2026-01`. Cloud's `AssertionIssuer` emits
`iss=Settings.environment_id`; the tenant deployment must render the matching
`VUORO_GATEWAY_ASSERTION_ISSUER` setting. An assertion must be a signed JWT
with `typ=JWT`, `alg=EdDSA`, that key id, the configured issuer and audience,
required `sub`/`actor`, `workspace_id`, non-empty
deduplicated authorities and repository IDs, `request_id`, `jti`, `iat`,
`nbf`, and `exp`. `sub` equals `actor`; `request_id` equals the single
`X-Request-ID` and the parsed invocation envelope's `request_id`; `jti` is a
printable token of at most 128 characters that need not equal `request_id`
(see "Replay protection"); the
workspace ID equals `VUORO_WORKSPACE_ID`; and every asserted repository is in
the mounted binding. Cloud issues `nbf=iat-2` and a maximum 30-second lifetime;
Vuoro permits at most that two-second `nbf` skew, accepts a 30-second
`exp-iat`, and rejects 31 seconds. Invalid assertions fail as the existing
identity-required response, without fallback to a static bearer registry.

If the gateway key is absent, static bearer mode remains the checked-in
compatibility path only for non-hosted/default bindings; a hosted binding
fails closed. Supplying both modes is rejected. Vuoro accepts Cloud's current
`VUORO_ENVIRONMENT` and `VUORO_<DOMAIN>_DSN` names as narrow compatibility
aliases for the canonical `VUORO_ENVIRONMENT_NAME` and
`VUORO_<DOMAIN>_RUNTIME_DSN` settings. Cloud still owns rendering those
settings and must add the environment class, workspace ID, issuer, and any
canonical names needed for a release; Vuoro does not infer environment policy
or workspace authority from a binding.

## Replay protection

Each gateway assertion is accepted once (agentops#2519). The first verifier of
an assertion consumes `(subject, jti)` in a bounded in-memory cache; a second
presentation is refused with 401 `identity-replayed` (the MCP edge answers 401
with JSON-RPC error `-32003`). Entries expire at `exp` plus the two-second
skew. The cache holds at most 50,000 unexpired entries per process; at that
cap it refuses new assertions (503 `identity-replay-capacity`, JSON-RPC
`-32004` at the edge) rather than evicting an unexpired entry, and logs the
refusal, at most once a minute. Only a fully verified assertion reaches the
cache, so a forged or malformed one can neither fill it nor spend another
caller's jti. The cache is per process: `vuoro-service serve` and
`mcp-serve` each pin uvicorn to one process (`workers=1`, which also
overrides `WEB_CONCURRENCY`), because every extra worker would be a separate
place to replay.

The first verifier depends on the route:

| route | first verifier | how later calls are authenticated |
|---|---|---|
| gateway -> runtime shell | the shell | none: one assertion, one invocation |
| gateway -> MCP edge -> shell | the edge | one-use edge proofs, below |

One MCP tool call can reach the shell several times with its one assertion:
`append_evidence` resolves the run, reads the chain tail and appends (three
shell calls), and on `evidence-chain-conflict` reads and appends again, up to
seven calls; `write_session_note` makes two. The shell therefore does not
consume a jti that arrives through the edge. Instead each edge -> shell call
carries `X-Vuoro-Edge-Proof`, an HMAC-SHA256 over a random nonce, its
issue/expiry time (10 s), the method, the path, SHA-256 of the forwarded
assertion and SHA-256 of the exact body, under a per-pod key. The shell still
verifies the assertion's gateway signature (the key cannot mint or widen an
identity), verifies the proof, and accepts each nonce once. A captured proof
cannot be replayed, carry another body or vouch for another assertion. After
a proof is accepted, the assertion's jti is also recorded in the shell's own
cache, so the assertion can no longer be used on the direct route.

The proof key is 32 random bytes at exactly `/run/vuoro/edge-proof/key`
(`VUORO_EDGE_PROOF_KEY_FILE`), a memory-backed `emptyDir` mounted into both
containers of the pod, which run as the same UID. Whichever process starts
first creates the file (mode 0600) atomically; the other reads it. It is not a
Secret and never leaves the pod. The edge refuses to start without it; the
shell verifies proofs only when it is configured.

While the edge and the shell share one audience, a shell configured for edge
proofs refuses a direct assertion that carries `client_id` or `grant_id`
(which only the gateway's OAuth `/mcp` path mints) with 401
`identity-edge-proof-required`, so an MCP assertion captured in the pod cannot
reach the shell without the edge. `VUORO_EDGE_GATEWAY_ASSERTION_AUDIENCE`
sets the edge's audience (default: the shell's). When it differs, the edge
expects it, the shell accepts it only with an edge proof, and the interim
`client_id` rule switches off.

**Restarts.** The caches live in process memory, and a restart empties them.
The TTL argument alone ("assertions expire in 30 s and a restart takes
longer") does not hold for a container restart: the kubelet's back-off before
restarting a container starts at 10 s or less, start-up takes seconds, and
an `emptyDir` (so the proof key) survives it, which is well inside the 32 s an
assertion can be accepted for.
So each verifier also refuses what a previous instance might have accepted:
the direct route refuses an assertion issued more than the two-second skew
before the process built its verifier, and the shell refuses an edge proof
minted before it started (the edge and the shell share the node clock, so no
skew applies). What remains is an assertion issued within two seconds of a
restart that completed in under two seconds, which is shorter than process
start-up. A new pod has new caches and a new proof key.

**Gateway follow-up (vuoro-cloud).** Correctness here does not depend on
`jti == request_id`, but the gateway must:

- mint `jti` itself, unique per assertion (for example a ULID or 128 random
  bits), instead of deriving it from the client's `X-Request-ID`. Today a
  client that reuses its request id gets two assertions with one jti, and the
  second is refused as a replay. Keep `request_id` equal to `X-Request-ID`;
- mint a fresh assertion for every forwarded request, retries included, and
  treat 401 `identity-replayed` / `-32003` as "re-mint and retry once", never
  as a reason to resend the same assertion;
- mint `/mcp` assertions with their own audience (for example `vuoro-mcp`,
  rendered to the runtime as `VUORO_EDGE_GATEWAY_ASSERTION_AUDIENCE`), so
  the shell refuses an MCP assertion on the direct route by audience rather
  than by the `client_id` rule;
- render the `/run/vuoro/edge-proof` memory-backed `emptyDir` into both
  containers and set `VUORO_EDGE_PROOF_KEY_FILE` in both.

