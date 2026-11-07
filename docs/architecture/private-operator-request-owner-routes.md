# Private operator-request owner routes: service identity boundary

**Status:** source-only integration scaffold; disabled by default. No service
key is provisioned, no runtime is released, and no private listener is enabled.

## Runtime placement

The source now builds owner routes in a separate owner-only ASGI app for a
dedicated loopback listener on port 8082. It is not mounted on the general
`vuoro-service serve` app at port 8080, and no Kubernetes Service port exposes
8082. The owner listener uses the shared work adapter to create fresh,
per-request database leases. The general shell remains available for
established runtime APIs. The separate `vuoro-mcp-edge` process on port 8081
deliberately has no DSN and rejects environments containing `*_DSN`; it must
not become an owner database proxy.

The Cloud tenant namespace source currently renders a default-deny ingress
policy admitting the Cloud gateway pod selector to the shell ports 8080 and
8081. That policy does not route to the new loopback owner listener. The
private HTTPS sidecar path remains a proposal in the next section. The gateway
assertion remains the end-user identity; it does not authenticate the Cloud
process. Owner routes also require a separate Cloud service assertion and a
process-owned private ingress guard.

## Private TLS transport proposal (not selected or qualified)

The current path cannot satisfy Cloud's HTTPS-only owner client: the shell
`serve` command has no TLS options, and the generated tenant Service and
NetworkPolicy expose HTTP port 8080 (plus MCP port 8081 when enabled). The
Cloud ASGI test origin is synthetic and gives no evidence about Kubernetes
transport. The following is a concrete candidate topology, not an implemented
or approved deployment:

1. Add an Envoy sidecar to the existing `vuoro-service` pod. Kubernetes
   containers in that pod share a network namespace; the current MCP edge
   already reaches the shell through `127.0.0.1:8080`. Envoy would listen only
   on the tenant Service's private 8443 port and forward owner paths to the
   dedicated owner-only loopback listener on 8082. Its Uvicorn config uses
   `proxy_headers=False`; the existing 8080 listener and its proxy-header
   semantics remain unchanged. The trusted Cloud adapter guard also requires
   the raw ASGI peer address to be loopback as well as verifying the signed
   service assertion. A direct Cloud-to-8080 request fails because owner
   routes are absent there. Envoy must not add an externally routed Service,
   Ingress, or public DNS record.
2. Envoy presents a per-workspace leaf certificate whose SAN is the exact
   private Service DNS name used by Cloud. Cloud verifies the certificate
   against a dedicated private CA bundle and uses that DNS name for SNI and
   hostname validation. Cloud's HTTP client keeps certificate verification
   enabled, redirects disabled, and environment proxies disabled.
3. Envoy requires a separate Cloud gateway client certificate from the same
   private trust domain. It validates client certificate chain and identity
   before forwarding. This transport identity remains separate from both the
   end-user gateway assertion and the short-lived Cloud service JWT. The
   Sprintctl receiver still verifies both assertions and its owner transaction
   still enforces current authorization and durable idempotency.
4. The tenant NetworkPolicy admits the Cloud gateway only to 8443 for this
   private path; it must not leave a network route from gateway pods to the
   owner HTTP route on 8080 that bypasses TLS. Existing 8080 consumers must be
   enumerated and preserved deliberately. Envoy-to-shell loopback must be
   checked against the deployed CNI, not inferred from an ASGI test.
5. A qualified private issuer must create short-lived per-workspace server
   certificates and a Cloud client certificate. Only Envoy receives the
   server private-key Secret; only Cloud gateway receives the client-key
   Secret; neither is logged or copied into ConfigMaps. Renewal must update
   Envoy without serving an expired certificate. CA rotation requires an
   overlap period where Cloud trusts old and new roots, leaf renewal under the
   new root, verification of active certificate fingerprints/expiry, then
   removal of the old root. Missing or expired material keeps the owner client
   and owner routes disabled.

No cert-manager installation, private issuer, CA secret lifecycle, Envoy image
pin, Cloud certificate distribution mechanism, or deployed dedicated owner
listener is present in the examined source. Those are prerequisites to
selecting this topology; this note does not assert that any exists or provision
any key material. The source now has a separate owner-only ASGI app factory and
a listener runner fixed to `127.0.0.1:8082` with `proxy_headers=False`; no owner
routes are mounted on the general 8080 app. This listener still cannot be
enabled by the ordinary CLI because it requires an injected process-owned
ingress guard. Local negative controls prove missing-guard refusal, loopback
binding, HTTP-origin rejection, and that no 8443 listener or TLS Secret is
rendered by the current tenant template.

## Independent service assertion

The Cloud delivery adapter signs `X-Vuoro-Cloud-Service-Assertion` with a
dedicated Ed25519 key. It sends the ordinary user assertion in
`X-Vuoro-Identity` separately. The service token carries only the Cloud service
subject, scalar audience `sprintctl-trusted-cloud-adapter/v1`, configured
issuer and key ID, workspace, exact `POST` path, SHA-256 of the exact request
body, JTI, and a lifetime of at most 30 seconds. It contains no actor,
principal, repository list, authorities, or OAuth claims.

The runtime verifier pins the public key, rejects a key equal to the gateway
user key or any recipient-admission key, and checks the signature, exact
scalar audience, workspace, method, path, and bounded request-body digest. The
verifier also requires the ASGI peer IP to be loopback; it does not trust a
caller-supplied forwarding header. The Envoy proposal must strip all
`Forwarded` and `X-Forwarded-*` headers so Uvicorn retains the local upstream
peer. The owner receiver reads the body incrementally under its 32 KiB limit,
then verifies the service assertion before JSON parsing. It separately
verifies the end-user gateway assertion and recipient-admission assertion
before the owner transaction.

The service assertion JTI cache is process-local. A restarted replica can
accept a still-valid token that another replica already saw; it is not a
global one-use mechanism. It authenticates the caller and binds the call, while
the user assertion replay resolver and durable owner delivery ledger retain
their separate responsibilities.

## Default-off composition and missing deployment proof

`VUORO_OPERATOR_REQUEST_DELIVERY_ENABLED` defaults to `false`. Enabling it
requires the mounted gateway verification key, separate service verification
key and issuer/key ID, recipient-admission public keyset and issuer, a callable
private-ingress guard supplied by the hosting composition, and the owner store
lease. Missing values fail closed. The ordinary `vuoro-service serve`
entrypoint does not supply a private-ingress guard, so setting the environment
flag alone cannot mount the routes. `mcp-serve` runs the separate edge process
and is not the owner-route host.

The application cannot inspect a Kubernetes NetworkPolicy decision from an
HTTP header or arbitrary request field. The current Cloud gateway-to-tenant
policy is source configuration, not proof that a specific deployed CNI applied
it. A reviewed hosting composition must supply a real ingress guard before the
routes can be enabled. A string environment marker or constant-true callback
is not such proof.

Cloud must provision the *private* service key only to its gateway workload
and the matching *public* key only to tenant owner runtimes. The two keys must
not reuse the gateway user assertion key or the recipient-admission key. This
source pass creates no key material and does not alter SOPS secrets, runtime
manifests, tenant compatibility pins, or migrations.

## Source compatibility gate

At this writing, Vuoro `vuoro-service` 0.1.104's immutable adapter lock points
to Sprintctl source revision `83cf461` (Sprintctl 0.28.0), which predates the
merged PR225 owner routes and PR226 trusted Cloud service guard (merged
Sprintctl source `815b63f6a2eed851689738b8887541badb048a86`, pending a
qualified releasable adapter artifact). Cloud's compatibility manifest still
pins `vuoro-service` 0.1.94 and tenant schema 2..3. The hooks import the owner
routers only after explicit enablement and fail closed when those modules are
absent. A future compatibility update needs a reviewed Sprintctl artifact and
lock, Vuoro runtime release, Cloud schema qualification and migration gates,
and verified private ingress/key provisioning. This source scaffold does not
claim those gates have passed.
