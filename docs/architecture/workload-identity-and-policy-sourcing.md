# Workload-identity and policy sourcing decision

Status: **bounded source decision; reference-workflow qualification pending**.
The current design retains the gateway as the hosted request authenticator and
the existing service/owner checks as authorization. It selects no additional
workload-attestation product and no policy evaluator. Do not install SPIFFE,
SPIRE, OPA, Cedar or another control plane on the strength of this note.

## What the current gateway authenticates

In hosted mode Vuoro verifies a Cloud-signed Ed25519 assertion at the tenant
runtime boundary. The verifier checks issuer, audience, workspace and mounted
repository scope, subject/actor consistency, principal epoch, authorities,
request correlation, issue/expiry bounds and a one-use `jti`. The gateway's
external OAuth processing and signed assertion authenticate the configured
user/client/grant path for a specific request. The service does not query Cloud
state; it enforces the asserted scope against its catalog and owner adapters.
The direct shell and MCP edge routes have separate first-verifier and replay
rules, including body-bound one-use edge proofs. These checks reduce replay
opportunity; they are process-local and are not a statement that a compromised
host or pod is trustworthy.

The current assertion contains no proof that a particular model, agent binary,
runtime process, native host or user-presence event produced the request. A
`client_id`, `grant_id`, actor string or reported runtime/model label must not
be promoted into workload attestation. Static bearer-registry mode also remains
a compatibility path for non-hosted bindings: tokens are hand-mapped to
principals, and that registry alone does not provide expiry, epoch rotation or
a durable reissue record.

## Threat boundary and residual risk

| Threat | Current control or limit | Residual statement |
| --- | --- | --- |
| Copy an assertion after it has been accepted by the same verifier process | One-use replay cache; bounded lifetime; route separation | A second accepted use is refused. The cache is in memory and host compromise is outside this guarantee. |
| Copy a valid assertion before first use | Signature, audience, expiry and scope are checked; first presentation consumes its `jti` | The verifier cannot distinguish the intended process from a thief racing the first use. |
| Copy the external bearer credential | The gateway applies its upstream OAuth/provider rules and can mint a bounded service assertion | Vuoro cannot revoke the upstream credential. Exposure lasts until the issuer or credential owner revokes/expires it; this boundary must be measured for the chosen workflow. |
| Compromise a host or pod that can read credentials or invoke the gateway | Existing OS, pod and gateway boundaries constrain ordinary access | A fully compromised host/pod can act through credentials and processes available there. A software identity agent on that same host would not by itself prove host integrity or stop that attacker. |
| Reissue a static principal or reuse an actor label | Hosted principal epochs are issuer-bound; static registry reissue is an explicitly different path | Static identity remains weaker and must not be described as equivalent to hosted principal issuance. |
| Broaden an authorized operation | Service catalog authorities, repository scope, owner checks and protected-side effect authorization | These are the current policy enforcement points. A policy engine would not replace owner validation, revocation or protected-side checks. |

The proposed run-scoped delegation in
[`run-scoped-delegation.md`](run-scoped-delegation.md) is design work, not a
current mitigation for same-host token access.

This is an explicit residual risk, not a claim that the current path has
workload identity. If the selected reference workflow requires proving *which
runtime* acted, or requires revocation at a target that cannot consult the
current issuer, the current assertions are insufficient. A host-local
SPIFFE/SPIRE agent would establish possession of a workload credential, not
trustworthiness of a compromised host. Hardware-backed or remotely attested
identity would need a named verifier, measured claims, failure behavior and
revocation proof before it could change that conclusion.

## Sourcing decision and qualification questions

Keep issuer selection with the provisioned gateway/OIDC owner and leave
hosted workload identity **unselected** until the EE-06 reference workflow names a
runtime-specific decision that changes authorization. At that point compare
existing short-lived gateway assertions, issuer-backed OAuth token exchange,
WIMSE profiles and any provider-issued identity against the actual enforcement
point. Require these checks before adopting one:

1. Identify which claims are signed by an independent issuer, which values are
   merely reported by a caller, and which authority decision consumes each.
2. Test copied-token use before and after first presentation, expiry, principal
   epoch change, verifier restart, issuer outage and host/pod compromise.
3. Show where revocation is observed, the maximum residual credential lifetime,
   and whether customer-controlled execution remains the effect boundary.
4. Ensure a runtime/provider change invalidates only the qualification that
   relies on that runtime identity. Do not bind identity to a model name.

The policy evaluator also remains **open**. Current catalog requirements,
repository restrictions, owner state machines and protected-side authorization
are explicit in code. There is no sourced reference-workflow policy corpus or
measured policy gap that justifies choosing Cedar, OPA/Rego or a custom
policy-as-data evaluator. A later decision needs a frozen policy input model,
allow/deny/unknown outcomes, explanations and audit lineage, failure behavior,
cost/operational bounds and a negative case for copied or widened authority.
Until then, retain the existing checks and do not add a second authority
engine.

The source evidence supports only this bounded decision: preserve the current
gateway/owner enforcement boundaries, expose the missing runtime-attestation
claim and copied-token/host residuals, and leave both component choices open
until a real reference workflow demonstrates their need and gives them a
falsifiable acceptance test.

## In-cluster API reference

LANECTL already uses the Sprintctl-owned `work.public.item-v1` operation to
read owner settlement in the `lanectl` repository. A dedicated read-only
observer can qualify that call with a projected Kubernetes token. The
scheduled coordinator keeps its existing runner credential for claim,
heartbeat and settlement writes. The hosted gateway and MCP edge retain
their request-specific assertion and proof contract.

The Vuoro service adds an optional `VUORO_KUBERNETES_IDENTITY_FILE` alongside
the local bearer registry. A file describes one explicit binding using the
`vuoro-kubernetes-identity/v1` schema. For example:

```json
{
  "schema_version": "vuoro-kubernetes-identity/v1",
  "environment": "example-shared",
  "api_server": "https://kubernetes.default.svc",
  "ca_file": "/run/kubernetes-reviewer/ca.crt",
  "reviewer_token_file": "/run/kubernetes-reviewer/token",
  "issuer": "https://cluster.example.com",
  "audience": "https://vuoro-shared.example.com/kubernetes",
  "service_account": "system:serviceaccount:lanectl:settlement-reader",
  "service_account_uid": "dcbdb702-279d-48b6-9b65-fd37f88a5547",
  "principal_id": "cluster-workload:lanectl-settlement-reader:1",
  "workspace_id": "lanectl-pilot",
  "repo_id": "lanectl"
}
```

The operator binds the ServiceAccount incarnation and a dedicated principal
to one repository and workspace. The resolver checks the asserted issuer,
exact username, single purpose-specific audience and expiry, then asks the
configured Kubernetes API server to authenticate the same token. It requires
the matching ServiceAccount UID and a validated Pod binding in the review
response. The shell permits only the repository-scoped read operation and
retains the normal catalog and owner checks. Hosted configuration refuses
this mode.

Grant the service reviewer only `create` on `authentication.k8s.io`
`tokenreviews`. Mount the Kubernetes API CA and a rotatable API-audience
reviewer token. Use metadata-level audit for TokenReview so request and
response bodies carrying tokens stay out of audit logs. A TLS or review
outage refuses workload requests. At most eight reviews run concurrently per
service process. A saturated reviewer returns HTTP 503. Existing static credentials continue to
resolve as before.

A workload token remains a bearer credential while the Pod exists. Copies
can succeed until Kubernetes rejects the token. Reviews run on each request,
so Vuoro adds no cache window to the Kubernetes revocation interval. Binding
a recreated ServiceAccount requires a new UID and a fresh principal epoch.
The reference verification is source-level. A deployed identity still needs
a live positive read and negative cases for a different ServiceAccount,
repository, operation and a deleted Pod.
