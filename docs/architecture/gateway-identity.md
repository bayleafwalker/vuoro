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
`nbf`, and `exp`. `sub` equals `actor`; `jti` and `request_id` both equal the
single `X-Request-ID` and the parsed invocation envelope's `request_id`; the
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
