# Run-scoped delegated credentials

Status: source implementation staged 2026-10-17; release, deployment and host
qualification remain pending. Store ownership remains with Sprintctl; resolver
and catalog composition remain with Vuoro. The work is motivated by
the agentops host adapter (agentops `scripts/host_adapter.py`, PR #343) and by
its bounded execution environment (agentops
`docs/architecture/bounded-execution-environment.md`, gitops-nixos
`profiles.agentopsHostAdapter`). This note builds on
`docs/architecture/agent-directory-and-inbox.md` (contract v1) and
`docs/architecture/gateway-identity.md`. It narrows the delegation idea in
`docs/plans/2026-10-14-agent-reachability-and-messaging.md` to one case: a
host's launcher handing a single run to the session it starts.

## 1. Problem

On devbox, the host adapter polls the inbox of one principal,
`vuoro-static:devbox-inbox-native:0`. For each `continue` message it does
three things as that principal:

* decides it, writing the `accepted` or `refused` receipts;
* registers a run with `work.run.register-v1`;
* asks `opencode serve` to start a session.

The session's own sprintctl calls (`sprintctl run context`,
`sprintctl run note`) must operate on that run. `_require_owned_run` /
`pg.resolve_run` resolves a run only for the exact binding it was registered
under: `(principal_id, workspace_id, client_id, grant_id)`. The session
therefore has to present **the same principal's credential**.

That credential is the inbox *decision* credential. The authority it carries
is `work:read` plus `work:evidence`, and `work:evidence` is enough to:

* write `work.message.receipt` (accept or refuse any message to that
  principal);
* call `work.message.send` and `work.message.cancel`;
* register, observe and retire directory instances;
* register further runs.

So a session, or anything else running as the `agent` uid that can read the
credential file, can decide inbox messages and start work. The adapter's
policy, environment record and uid separation are then bypassed one level
down. Today the agentops bounded-environment docs record this as the
remaining gap, and devbox keeps `launchesEnabled = false` until it is closed.

The goal is that **the session never holds the decision credential**. It
holds only a credential that can act within the one run it was launched for,
through the few run-scoped operations it needs, and only until that run ends.

## 2. Options considered

| Option | What the session holds | Why not (or why) |
|--------|------------------------|------------------|
| A. Separate static principal for sessions | its own static token | The run is bound to the adapter's principal, so the session cannot resolve it (`run-not-found`). Registering the run under the session principal instead would hand the session `work.run.register-v1`, and with it the ability to start runs. One shared session token is also not run-scoped. |
| B. Per-run principal (S7) | a freshly minted principal per run | This is the general answer to delegation, already deferred in the reachability plan (§S7). It needs principal lifecycle, epoch handling and directory changes, which is too much for this gap. |
| C. Run-scoped delegated credential (**proposed**) | an opaque token bound to one run, one operation allowlist and an expiry, minted by the owner on the adapter's request | It keeps the run's binding, so no store migration of run identity is needed. It reuses the owner's existing run resolution, and it can be revoked when the run settles. |
| D. Gateway JWT with a narrowed authority claim | a 30 s assertion from Vuoro Cloud | Devbox's vuoro-shared runs the static resolver, and the two modes are exclusive (`composition.py:1058-1081`). Authorities alone also cannot express "only run R". |

## 3. Design

### 3.1 Delegation record (Sprintctl, store owner)

A new owner table holds one row per delegation:

| Field | Meaning |
|-------|---------|
| `delegation_id` | `dlg_<ULID>` |
| `repo_id`, `run_id` | the one run this delegation acts within |
| `parent_binding` | the run's `(principal_id, workspace_id, client_id, grant_id)`, copied from the run at mint time |
| `operations` | closed allowlist; v1 permits only the run-scoped set in 3.3 |
| `expires_at` | owner clock; integer TTL 1–14400 seconds, bounded by verified parent credential expiry when present |
| `token_sha256` | digest of the bearer secret; the secret itself is never stored |
| `revoked_at`, `revoked_reason` | set by revoke, by run settlement or by parent retire |
| `created_by`, `created_at` | the parent identity that minted it |

### 3.2 Operations

| Operation | Caller | Authority | Effect |
|-----------|--------|-----------|--------|
| `work.run.delegate-v1` `{run_id, operations[], ttl_seconds, idempotency_key}` | the run's own binding (the adapter) | `work:evidence` | Requires `_require_owned_run(run_id)`. Refuses an operation outside the v1 allowlist (`delegation-operation-not-allowed`), and refuses if the run already has an unrevoked delegation (`delegation-exists`; one live delegation per run). Returns `{delegation_id, token, expires_at}`. The token is returned **once**; an idempotent replay returns the record without the token, so a crash between mint and hand-off is resolved by revoking and minting again, never by re-reading the secret. |
| `work.run.delegation-revoke-v1` `{delegation_id, reason, idempotency_key}` | the parent binding | `work:evidence` | Sets `revoked_at`. Idempotent. |
| `work.run.delegation-get-v1` `{delegation_id}` | the parent binding | `work:read` | Returns the record without the token (diagnosis). |

Settlement also revokes a run's delegation: the inbox `result-linked`
receipt for the run and `work.agent.retire` of the exact creator instance
revoke without a separate call. Session notes have no typed terminal semantics
and therefore do not trigger settlement or revocation.

### 3.3 What a delegated identity can do

The v1 allowlist is closed. These operations, all run-scoped, are permitted
only when the `run_id` argument equals the delegation's `run_id`:

* `work.run.resolve-v1`
* `work.run.predecessor-context-v1`
* `work.session-note.write-v1`
* `work.evidence.append-v1`

Every other operation is refused with `delegation-operation-not-allowed`
before it reaches the owner. That includes all of `work.message.*`,
`work.agent.*`, `work.run.register-v1`, `work.run.delegate-v1`,
`work.decision.record`, effect and attempt operations, and reads outside the
run. A delegated identity can never mint another delegation (depth 1).

### 3.4 Resolution (Vuoro service)

Delegation tokens get a distinct shape, `vrd_<base32>`, so they cannot be
confused with static tokens. A `DelegatedBearerIdentityResolver` is
**chained in front of** whichever primary resolver is configured, static or
gateway. It handles only `vrd_` tokens and passes every other token through
unchanged. Composition stays exclusive for the primary resolvers; the
delegation resolver is an addition, not a third mode.

For a `vrd_` token the resolver:

1. hashes the token and asks the owner for the live record
   (`delegation-resolve`, an internal owner call, not in the public catalog);
2. refuses with `credential-invalid` if the record is unknown, revoked or
   expired;
3. builds an identity with:
   * `principal_id`, `workspace_id`, `client_id`, `grant_id` from
     `parent_binding`, so `_require_owned_run` works unchanged for the one run;
   * `actor = "delegation:<delegation_id>"`, so audit shows that a
     delegation acted, not the parent;
   * `authorities = {work:read, work:evidence}`, an intersection that is
     never wider than the parent's;
   * a new identity field `delegation = {delegation_id, run_id, operations}`.

The shell then refuses any operation not in `delegation.operations`, and any
`run_id` argument that differs from `delegation.run_id`. It does this before
dispatch, at the same point where `required_authority` and repo scope are
checked (`app.py:397-404`). The owner checks again: every handler that
receives a delegated context re-verifies the record (still live, same run).
Like the lease and effect suites, this is checked twice and does not trust
the edge.

Audit and idempotency: idempotent writes by a delegation are keyed by the
parent principal (unchanged ledger scope). The digest binding includes
`delegation_id`, so a delegated write and a parent write with the same key
conflict instead of replaying into each other.

### 3.5 Hand-off on the host

The adapter mints the delegation after `run-linked` and before it creates
the session. It writes two files into the run's working directory: a client
profile and the token. That directory is the per-run worktree under the
adapter-owned root (gitops-nixos #23). The files are:

* `<run dir>/.vuoro/profile.json`: a `vuoro-client-profile/v1` profile with
  `credential_ref: "file:<run dir>/.vuoro/credential"`;
* `<run dir>/.vuoro/credential`: the `vrd_` token, owned by
  `agentops-adapter`, group `agent`, mode 0640, in a directory the session
  cannot replace (sticky, adapter-owned).

sprintctl finds the profile through the existing `SPRINTCTL_VUORO_PROFILE`
variable, set per session where the harness allows it. Otherwise it uses a
new lookup of `.vuoro/profile.json` in the working directory: an exact path,
not an ancestor walk, and refused if the file is writable by the caller.

The `opencode-serve-inbox` unit then no longer binds the inbox credential at
all. The decision credential is readable only by `agentops-adapter`
(sops-nix, 0600), and the agent-readable copy is deleted and its principal
epoch bumped.

When the run settles, the adapter revokes the delegation (and settlement
revokes it in any case), then removes the run directory.

### 3.6 Failure behaviour

| Situation | Behaviour |
|-----------|-----------|
| Mint fails | The run is closed `not-started`, the same as any pre-launch denial. |
| Adapter crashes after mint, before launch | On recovery it revokes any live delegation for the run, then mints again. Tokens are never re-read. |
| Token leaks | It acts only within one run, through four operations, until revocation or expiry. It cannot decide messages or start work. |
| Owner unreachable during resolution | `credential-invalid` (fail closed). |

## 4. What this does not solve

* The session can still write notes and evidence for its own run, including
  misleading ones. Evidence evaluation, not authentication, handles that.
* Static identities still have no expiry or epoch rotation without a restart.
  Delegations have both, but the parent credential does not.
* Delegation across principals (the reachability plan's delegation mode) is
  still deferred. This design is the depth-1, same-binding case only.

## 5. Implementation order

1. **Sprintctl:** the delegation table and migration, `work.run.delegate-v1`,
   `-revoke-v1` and `-get-v1`, the internal `delegation-resolve`, owner-side
   re-verification in the four run-scoped handlers, revocation on settlement,
   and conformance tests (mint, use, refuse an operation outside the
   allowlist, refuse another run, expiry, revoke, depth 1).
2. **Vuoro:** the `vrd_` resolver chained in composition, shell enforcement of
   `delegation.operations` and `run_id`, catalog entries, and the conformance
   suite in `packages/vuoro-service/tests/conformance/`.
3. **sprintctl client:** the `.vuoro/profile.json` lookup in the working
   directory.
4. **agentops host adapter:** mint after `run-linked`, write the run
   directory files, revoke on settlement, and recovery as in 3.6.
5. **gitops-nixos:** stop binding the inbox credential into
   `opencode-serve-inbox`, rotate the inbox principal epoch, and only then
   enable launches on devbox.

## 6. Staged source consumer and remaining gates

The shell implementation is `vuoro_service.run_delegation`. It composes the
released work application's internal `resolve_run_delegation_digest` API,
passing only SHA-256(token). The owner resolves the digest within its configured
database and returns the exact repository and parent binding. The shell keeps
the immutable owner context, narrows authorities, checks expiry and the closed
operation/run pair, and applies normal repository checks. Hosted composition
also requires the configured workspace and project-repository boundary, so a
digest lookup across the owner database cannot bypass tenant routing. Every
request resolves current owner state; the owner revalidates again in the action
transaction, including a cached context after revocation.

`Identity.expires_at` preserves a verified gateway assertion's expiry for the
owner's mint bound. An assertion cannot mint a child that outlives that bound.
Static principals without a verified expiry retain the documented parent
rotation/revocation limitation; the child remains bounded by owner TTL and
explicit owner revocation. No authenticated workload/host identity is inferred
from the actor, model, harness or provider description.

Parent binding is a scope snapshot, not live upstream grant introspection. If an
OAuth grant or parent identity is revoked after mint, that event alone does not
revoke an already-issued run credential. The operator or adapter must revoke
the child through Sprintctl, or wait for a settlement/result-linked receipt,
retirement of the exact creator instance, or the child's bounded owner TTL to
end. A verified gateway expiry limits how long the child may be minted for; it
does not propagate an upstream revocation event after mint.

The immutable composition pin is unchanged. Older owner releases have no
lookup API and reserved `vrd_` credentials fail closed without falling back to
static or gateway authentication. This source support does not expose a new
operation before a qualified owner release is composed. Schema27 migration,
release/pin selection, host handoff/profile lookup, decision-credential removal
and epoch rotation, real harness continuation, and enabling launches are
separate qualification/deployment steps. No migration or launch is authorized
by this source implementation.
