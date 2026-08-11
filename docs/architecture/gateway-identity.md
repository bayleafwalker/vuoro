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

The optional issuer, audience, and key-id settings default to Cloud's current
contract: `vuoro-cloud`, `vuoro-service`, and `gateway-2026-01`. An assertion
must be a signed JWT with `typ=JWT`, `alg=EdDSA`, that key id, the configured
issuer and audience, required `sub`/`actor`, `workspace_id`, non-empty
deduplicated authorities and repository IDs, `request_id`, `jti`, `iat`,
`nbf`, and `exp`. `sub` equals `actor`; `jti` and `request_id` both equal the
single `X-Request-ID`; the workspace ID equals `VUORO_WORKSPACE_ID`; and every
asserted repository is in the mounted binding. Invalid assertions fail as the
existing identity-required response, without fallback to a static bearer
registry.

If the gateway key is absent, local/development static bearer mode remains the
checked-in compatibility path. Supplying both modes is rejected. The Cloud
deployment must separately align its generated environment and DSN variable
names with Vuoro's canonical `VUORO_ENVIRONMENT_NAME` and
`VUORO_<DOMAIN>_RUNTIME_DSN` contract; that deployment mapping remains Cloud's
ownership and is not silently absorbed by this service.
