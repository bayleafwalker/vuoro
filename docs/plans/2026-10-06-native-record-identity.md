# Explicit native record identity: MI-1 prerequisite

Operator-authorized implementation on 2026-10-06; served agentops item 2618,
prerequisite for item 2613. This is the standalone/private native record path,
not a hosted execution path or creation of a Cloud workspace.

## Measured blocker

A real `work.run.register-v1` request using the existing workstation profile
returned `identity-unbound`. The mounted static-registry loader admits a minted
principal but has no workspace field; its resulting identity cannot satisfy
the owner's required principal/workspace binding. The provider capture is real,
but it is not yet an authoritative evidence append.

## Contract and implementation

The existing `vuoro-identities/v1` mounted registry accepts optional
`workspace_id` on an issuer-controlled identity entry. Its value is an explicit
opaque standalone workspace partition from the credential issuer. It is not a
Cloud workspace object, routing declaration, public argument or inferred alias
of actor/environment/repository. An omitted field remains unbound. Explicit
null, empty, whitespace-containing or non-string values are refused at load.
The minted principal requirement, environment/repository checks and authority
list retain their current meaning. No capability is added by naming a workspace.

The static resolver supplies this value on its authenticated `Identity`; a
caller cannot choose a different workspace through invocation arguments or
request headers. Existing native run registration/resolution and evidence
append bind that identity using their published owner checks. OAuth
client/grant remain absent on this direct bearer path; no hosted assertion is
created or accepted as a consequence.

## Commissioning and acceptance

Appservice owns the encrypted identity registry on steady-state main. After
reviewed source, immutable release/image verification and compatible GitOps
repin, commission a **separate** opaque credential with a newly issued stable
principal, explicit native workspace partition, repository `agentops`, and only
`work:read` / `work:evidence`. Retain it in a separate mode-0600 native credential
file, referenced by an explicit native profile. The old workstation credential
must not be rebound, reused as a new principal or silently widened. Record
public identifiers and receipt digests; never the credential value.

Acceptance requires a real authenticated run register/resolve, followed by the
captured provider observation's native evidence append and exact retry. The
registered metadata must keep unobserved harness build and instruction facts
unknown. The shared service's role, schema and migration compatibility checks
remain required. Public effect accept/apply remains refused; the evidence
credential must carry neither authority. Rollback removes only the newly
commissioned credential/config reference and runtime repin, without reassigning
old work/run identities or erasing owner history.

Static-source tests cover explicit binding, omitted-binding non-inference and
invalid metadata refusal. Existing owner tests cover immutable run binding,
wrong-grant refusal, durable append replay and public effect refusal. Source
landing alone does not prove release, deployment or native commissioning.
