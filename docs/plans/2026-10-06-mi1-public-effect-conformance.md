# MI-1 public/protected effect conformance increment

This closes the named P3 HTTP refusal case against the declared authority
contract. It adds tests and bindings; production code and release pins do not
change. P2 and the other milestone requirements remain open.

The same portable test executes real `/api/invoke/v1` requests through the
Vuoro service shell, with the reference intent store and the published
Sprintctl owner on disposable PostgreSQL. The PG binding registers the
owner's complete released catalog, without copying its schemas or transition
logic. Only fixture setup uses owner storage helpers. Observations use the
owner API.

The public test identity has the proposed TS-16 read/record/coordinate/propose
maximum plus ordinary work write and effect propose/get rights. It lacks the
existing private accept and mark-applied capabilities. Even a caller-controlled
`X-Vuoro-Authorities` header naming those rights cannot grant them. Both
protected operation requests must return HTTP 403 `authority-required` before
any owner invocation, preserve the original intent and leave work pending.
A separate protected identity succeeds through the same registered route,
providing a positive control. Mark-applied records a synthetic application
receipt; it does not perform a Git or other external effect.

A deliberate mutant removes the service's pre-owner authority requirement.
The reference owner still refuses the operation, but the unchanged oracle
rejects the mutant because it invoked the owner. Thus an eventual refusal at
a later horizon cannot masquerade as the required transport refusal.

The identities are test bearer fixtures representing declared authority sets,
not tokens minted by the live OAuth issuer. These cases qualify the reusable
HTTP and published-owner boundary under those sets. They do not establish
production OAuth issuance, deployment, protected artifact verification or
actual effect execution. The current Cloud issuer's scope tests remain its
own authority; no apply scope is introduced.

Qualification and exact test counts are recorded in the versioned
[context](../../verification/contexts/mi1-public-effect-boundary.json) and
[result](../../verification/results/mi1-public-effect-boundary-2026-10-06.json).
The P3 neutral provider-ingestion replay case and P2 released raw-artifact /
protected-verifier link still need implementation and evidence.
