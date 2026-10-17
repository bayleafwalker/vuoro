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
workload identity as **unselected** until the EE-06 reference workflow names a
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
