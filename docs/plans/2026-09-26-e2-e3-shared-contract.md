# E2 / E3 shared contract (2026-09-26)

**Purpose.** agentops#2466 (E2) and agentops#2467 (E3) are built in parallel on independent branches. This page pre-resolves everything they share, so neither waits on the other and their merges conflict only textually.

**Rules.**
- Where this page decides something, neither branch reopens it.
- A change to this page is its own PR, merged before either branch relies on it.

**Operator decisions** (agentops `docs/plans/2026-09-26-cloud-enablement-plan.md`) bind both:
- cloud sessions push branches and open PRs only;
- runs and evidence come first, and claims only on an exclusive, owner-backed lease;
- single repository per run;
- effects are proposed on the public surface and executed only by a trusted service.

## 1. Actor attribution (pre-resolves agentops#2517)

**Decided: `actor = external_subject` (e.g. `github:77`) on both the OAuth `/mcp` path and the PAT path.**
- `principal_id` (`<issuer>:<users.id>:<epoch>`) stays as it is on both paths, and stays the ownership key for runs.
- `actor` is the human-readable attribution that provenance records.

**E2 owns applying it:**
- the vuoro-cloud gateway's `/mcp` assertion minting;
- a test that both paths mint the same `actor` and `principal_id` for the same user.

## 2. Scopes and authorities (fixed)

| OAuth scope | Assertion authority | Edge bucket | Tools | Owner |
|---|---|---|---|---|
| `vuoro:work.read` | `work:read` | `read` | `list_ready_work`, `describe_work` | live |
| `vuoro:evidence.record` | `work:evidence` | `record` | `register_run`, `append_evidence`, `write_session_note` | E2 |
| `vuoro:work.claim` | `work:claim` | `coordinate` | `claim_work`, `heartbeat`, `report_outcome` (was `complete_work`; §6 agentops#2540 amendment) | E2 |
| `vuoro:effect.propose` | `effect:propose` | `propose` | `propose_effect`, `get_effect` | E3 |
| `vuoro:effect.apply` | none | none | none | refused forever |

- **Mutating.** All three new authorities are mutating. vuoro-cloud `api.is_mutating_authority` must return true for them, so read-only and frozen workspaces refuse them.
- **Mutation freeze.** It must cover every tool above except the read bucket. Today `/mcp` skips the freeze; E2 fixes that for all write buckets at once.
- **Grants.** Each scope becomes grantable only for the pre-registered `claude-connector` client. The row is added to `SCOPE_TO_AUTHORITIES` by the item that owns the scope. vuoro-cloud `MCP_TOOL_SCOPES` gets one row per tool.
- **vuoro-cloud file layout.** E3 adds its rows in its own block below E2's, with a `# E3` comment, so a merge conflict is at most adjacent-line.

## 3. The edge seam (landed with this page)

`packages/vuoro-mcp-edge` gives each item its own modules. Neither item edits `server.py`, `toolsets.py`, `runs.py` or `idempotency.py`. A needed change there is a separate PR against this contract.

| Module | Owner | What |
|---|---|---|
| `toolsets.py` | shared | `ToolSpec`, `ToolSet`, `ToolsetContext`, `ToolFailure`, `BUCKET_AUTHORITIES`, `WRITE_ANNOTATIONS` |
| `runs.py` | shared | `RunBinding`, the `RunRegistry` protocol, `binding_for`, `UnavailableRunRegistry`, `InMemoryRunRegistry` (reference, tests only) |
| `idempotency.py` | shared | key shape, `request_digest`, the `IdempotencyLedger` protocol, `replay_or_conflict`, `InMemoryIdempotencyLedger` (reference) |
| `record_tools.py` | E2 | `build_toolset` for the record bucket |
| `claim_tools.py` | E2 | `build_toolset` for the coordinate bucket |
| `effect_tools.py` | E3 | `build_toolset` for the propose bucket |
| `composition.py` | E2 (one line) | swaps `UnavailableRunRegistry()` for its durable registry |

**How the seam behaves.**
- **Dispatch.** Toolset tools are dispatched like the built-in tools:
  - the caller's assertion must carry the bucket's authority;
  - results carry the 2026-07-28 envelope (`resultType: "complete"`);
  - a `ToolFailure` becomes an `isError: true` result with a stable `code`.
- **Authorities at the door.** The edge accepts only the authorities of the tools it actually registered. An assertion carrying `work:evidence` is refused while no record tool is registered.

**Amendment (2026-09-26): the `RunRegistry` protocol takes the caller's forwarded identity.**
- Why: E2's durable registry reaches the run owner through the runtime shell as the caller, so it cannot answer without the caller's assertion. The original protocol had no parameter for it. E2 first widened only its concrete store, which broke every caller that holds `context.runs` as the protocol (vuoro#131 review).
- The protocol in `runs.py` is now:
  - `register(binding, *, idempotency_key, forwarded, manifest) -> run_id`. `manifest` holds the RunManifest fields of the run record: `harness_id`, `harness_build`, `model_id`, `recipe_id`, `observed_profile`.
  - `resolve(run_id, caller, *, forwarded) -> RunBinding`.
  - `forwarded` is the `ForwardedIdentity` the toolset handler received, passed through unchanged, and `caller` is `binding_for(forwarded)`.
- `UnavailableRunRegistry` and `InMemoryRunRegistry` take the same parameters. The reference registry ignores `forwarded` and `manifest`.
- Every `RunRegistry` implementation must match these signatures exactly. A test runs the registry that `composition.py` wires in through a protocol-typed toolset call, so a mismatch fails in CI.
- E3 passes `forwarded=` to `runs.resolve` when it rebases.

## 4. Run handles

**Minting (E2).**
- `register_run` mints `run_<ULID>`.
- The run is bound to a `RunBinding`: principal, workspace and exactly one repository, plus OAuth client and grant once the gateway asserts them. E2 adds those two claims to the `/mcp` assertion.
- The same idempotency key under the same binding returns the same run.

**Resolving.**
- `resolve(run_id, caller)` returns the binding only to the exact same binding.
- Anything else — unknown id, malformed id, expired run, or someone else's run — is `run-not-found`, with one message for all cases, so callers can't probe which ids exist.

**Amendment (2026-09-27, agentops#2525): continuation across identities (TS-8 second route).**
- Why: TS-8 needs a run continued on another harness, and resolving only to the exact same binding means no other identity can pick a run up. Resolving stays exact; continuation is a separate, explicit link made when the successor registers.
- `register_run` takes an optional `predecessor_run_id`. The `RunRegistry` protocol's `register` gains the keyword `predecessor_run_id: str | None = None`; every implementation must accept it.
- **Eligibility** (agentops#253 R4 decision 3). The successor's binding shares the predecessor's `workspace_id` AND repository, AND its assertion carries `work:read`. A different principal, OAuth client or grant is legitimate.
  - The edge checks `work:read` before it calls the registry (`authority-required`).
  - The registry checks workspace and repository, records the link, and otherwise refuses with `predecessor-not-eligible`. Unknown, malformed, other-workspace and other-repository ids share that one code and one message, as `run-not-found` does.
  - The same idempotency key with a different `predecessor_run_id` is `idempotency-conflict`.
- **Reading.** `read_predecessor_context(run_id)` is a read-bucket tool (`work:read`). `run_id` is the successor's own run, resolved to its exact binding. It returns the predecessor's session notes and evidence, and `predecessor_run_id`. It reads one hop only, and never through the predecessor's handle.
- **Continuation transfers context, not authority.**
  - `resolve` is unchanged, so the predecessor's run never resolves to the successor. The successor cannot write notes or evidence to it, or propose an effect against it.
  - The successor's own run is bound to the successor's own binding. Every operation still needs the successor's own grant (`work:evidence`, `effect:propose`, …).
- **Served only where the owner supports it.** A record store advertises `predecessor_run_id` and `read_predecessor_context` when it sets `supports_continuation`, or, for a store whose owner may or may not offer it, when that owner advertises it at request time (the toolset's `describe` hook; `tools/list` omits the tool and `tools/call` answers `unknown-tool` otherwise).
  - `InMemoryRunRegistry` implements the rules and is the reference.
  - `SprintctlRecordStore` detects it: continuation is served when the runtime shell's catalog advertises `work.run.predecessor-context-v1` (sprintctl 0.11.0, schema 20, agentops#2525), which ships with `predecessor_run_id` on `work.run.register-v1`. A catalog that was read is kept for the process. Until then at most one read is in flight, bounded at 1 s (not the upstream timeout); a failed or timed-out read hides continuation and is not retried for 3 s, so a request costs at most one bounded read. The edge declares `tools.listChanged: false` and cannot notify (stateless HTTP), so the list may change once, when the catalog is first read; clients see it on their next `tools/list`. Against sprintctl 0.10.x and earlier nothing changes: neither is advertised.
  - sprintctl records the link under these eligibility rules (`run_predecessor`, schema 20) and reads a predecessor's notes and evidence only through the successor's own run. `predecessor_run_id` is sent only when set, so a register request without one keeps the digest sprintctl stored for it earlier.
  - Enabling it on vuoro.cloud needs, in one generation, a tenant runtime built with sprintctl 0.11.0 and a `MCP_TOOL_SCOPES` row `read_predecessor_context` -> `vuoro:work.read`.
- §6 (claims) is not changed by this amendment.

**Implementations.**
- The edge holds no credential and no DSN. E2's durable `RunRegistry` reaches the run and evidence owner through the runtime shell's invoke API, the same way `ShellWorkSource` reads work.
- The run record is the Phase 1 RunManifest (vuoro `docs/plans/2026-09-19-agentic-pipeline-first-principles-rebuild.md`), stored where that plan puts it. E2 must not fork a second evidence chain.

**E3's side.**
- Every effect intent carries a `run_id`, resolved through `context.runs`.
- Until E2's registry is wired in, `UnavailableRunRegistry` makes `propose_effect` fail closed with `runs-unavailable`.
- E3's tests use `InMemoryRunRegistry`.

## 5. Idempotency (both items)

- Every write tool requires `idempotency_key`: 8–128 characters from `A-Z a-z 0-9 . _ : -`. Use `IDEMPOTENCY_KEY_SCHEMA` in the input schema.
- The ledger is keyed by (workspace, principal, tool, key) and stores `request_digest(tool, arguments-without-key)` together with the first result. *(Amended 2026-09-26; was (workspace, tool, key).)*
  - Same key, same digest: replay the stored result, with no second effect.
  - Same key, different digest: `idempotency-conflict`.
- The first write wins atomically, and a racing writer gets the stored row back.
- Each item's ledger lives with its record owner, not in the edge. Both use the shared protocol and must pass behaviour tests equivalent to `InMemoryIdempotencyLedger`'s.

**Amendment (2026-09-26): the ledger key includes the principal.**
- Why: with a key of only (workspace, tool, key), two principals in one workspace share a key space. One principal could replay another's stored result by reusing their key. It could also probe which keys exist, because a different digest returns `idempotency-conflict` instead of a fresh write. Keying by principal removes both.
- This matches E2's ledger in sprintctl (sprintctl#97), which keys on (workspace, principal, tool, key).
- ~~`idempotency.py`'s `IdempotencyLedger` protocol and `InMemoryIdempotencyLedger` still take `workspace_id` only.~~ Superseded by the amendment below.

**Amendment (2026-09-27, agentops#2520): the shared protocol takes the principal.**
- `IdempotencyLedger.lookup(workspace_id, principal_id, tool, key)` and `store(workspace_id, principal_id, tool, key, stored)`. `InMemoryIdempotencyLedger` keys its rows the same way. No ledger folds the principal into another argument any more; the intent store passes it straight through.
- One behaviour test, `packages/vuoro-mcp-edge/tests/test_ledger_behaviour.py`, runs against every ledger the edge has: the in-memory reference and the intent store's ledger path. A new ledger joins by adding a factory there. The lease owner's ledger is sprintctl's `work_idempotency_ledger` (sprintctl#97), keyed (repo, workspace, principal, tool, key) and proved against PostgreSQL on the sprintctl side.
- Two claim tools are exceptions to the rules above, both decided by §6:
  - `heartbeat` has no `idempotency_key` (sprintctl's input schema forbids one). Refreshing a lease has no effect a retry could double, and the owner refuses a heartbeat on a dead lease whatever a key would say.
  - `claim_work` uses the ledger for conflicts only. A same-key, same-digest request is **re-evaluated by the owner, not replayed** (§6 decision 5): that is how a restarted worker resumes. The edge keeps no ledger for it and never answers it from a stored result.

**Amendment (2026-09-30, intent-driven correction; disposition register `actionq.goal_state.amended.open_resolved`).**
- The protocol shape is R4 lease semantics 6's `begin(workspace_id, principal_id, tool, key, request_digest) -> LedgerEntry` (agentops `docs/plans/2026-09-27-backlog-ideation.md`:249, normative). sprintctl already exposes it (`LedgerEntry`, `PgIdempotencyLedger`, agentops#2542); the edge's `lookup`/`store` above become adapters over it (agentops#2594).
- The sprintctl ledger is not outside this protocol. The operator needs the authority-plane abstraction enabled in a provider, not only inside one host, so the durable ledger joins this behaviour test as a binding that drives sprintctl's served operations on PostgreSQL (agentops#2594). sprintctl's own PostgreSQL tests remain its incumbent-specific proof (long-term direction §7.1, layer 2), not the contract. This supersedes "proved against PostgreSQL on the sprintctl side" in the bullet above as the conformance statement.

## 6. Claims (E2)

**Lease semantics.** `vuoro_service/lease.py` (the E0 `LeaseStore`) is the behaviour spec:
- expiry with heartbeat;
- the lease id, not the holder, is what counts as "current";
- a superseded lease id is permanently dead;
- a heartbeat or completion replayed against a dead lease fails even when it comes from the former holder.

**What it lacks.** The store is in memory only, so it is not the durable owner.

**E2 builds** `claim_work`, `heartbeat` and `report_outcome` (named `complete_work` before the agentops#2540 amendment) on a durable implementation of that same contract.
- The owner must respect `13-REPO-OWNERSHIP-AND-CHANGE-MATRIX.md`: sprintctl owns work semantics. So the durable lease is either sprintctl's claim machinery exposed as a public work contract, or a durable `LeaseStore` behind the shell that sprintctl's item state defers to.
- It is validated by `lease.py`'s contract tests, parametrized over both implementations.
- The (handle, auth_context) pair is validated as a whole: the lease holder is the run's `RunBinding`, not just the token.
- The advisory `reserve_work` is a separately named operation and is not part of E2.

**If the durable owner can't be built inside E2's branch,** E2 keeps `vuoro:work.claim` known but not granted. It ships run and evidence recording only, plus a short design note naming exactly what the lease owner must provide.

**Amendment (2026-09-27, agentops#2520): the durable lease owner and its decisions.**

The owner is sprintctl 0.9.0 (remote schema 18, sprintctl#98). It uses a new `work_lease` table, not the advisory reservation: reservations report overlap and never refuse, and a lease must refuse. The table is keyed by work item, and at most one lease per item is active. Reservations stay advisory and separate, and neither refuses the other. The reservation overlap report does not see leases, and a claim does not see reservations. That is accepted because the two answer different questions: a reservation says who intends to work, and a lease says who may settle. Only maintenance activation counts both. This answers the open question in `2026-09-26-e2-claims-design-note.md`.

If sprintctl#98 changes in review, this section is re-checked against its merged code before this page merges.

| Edge tool | sprintctl operation | Authority |
|---|---|---|
| `claim_work` | `work.lease.acquire-v1` (`item_id`, `run_id`, `idempotency_key`; `ttl_seconds` is refused from sprintctl 0.10.0, agentops#2540) | `work:claim` |
| `heartbeat` | `work.lease.heartbeat-v1` (`lease_id`, `run_id`) | `work:claim` |
| `report_outcome` | `work.lease.report-outcome-v1` (`lease_id`, `run_id`, `outcome`, `idempotency_key`; optional `summary`, `payload`, `checks`) | `work:claim` |
| none for now (§2 is unchanged) | `work.lease.read-v1` (`item_id`): leases and outcome reports, for evidence views | `work:read` |

`work.lease.complete-v1` is the deprecated alias of `work.lease.report-outcome-v1` from sprintctl 0.10.0 (same input, result and ledger tool). The edge never calls it.

The retired `work.claim.*` operation names stay retired.

Decisions (the #2520 checklist; the reasoning is in sprintctl#98). TS-1 is agentops `docs/plans/2026-09-17-target-state.md` TS-1. It permits `claim_work`, and it bars Vuoro from acting as a runner, queue or worker supervisor. Its five excluded verbs (assign, schedule, retry, supervise, expire) are stated for E3 intents. For leases, this page reads TS-1 as: the owner evaluates a lease only when a caller calls, and nothing runs in the background.
1. **Expiry is evaluated by the owner, when someone calls.** At claim, heartbeat and completion, sprintctl checks `heartbeat_at + ttl_seconds <= now` against its own database clock. Nothing sweeps, and the edge never schedules, expires or retries. The TTL defaults to 300 s. `SPRINTCTL_LEASE_TTL_SECONDS` sets it per runtime, which is per workspace because each tenant runtime serves one workspace; the value is clamped to 30–3600, and a bad value falls back to 300. A claim may ask for 30 to 3600 s. As `lease.py` specifies, an expired lease can neither heartbeat nor complete, even if nobody took it over.
2. **Takeover is a claim of a stale lease.** A fresh lease is `lease-held`. There is no separate operator-reassignment operation yet. An operator ends a lease by recording a terminal decision on the item, since any terminal decision ends the lease. Moving an item off `active` does not end it: the holder gets `work-not-active`, and everyone else is refused (`lease-held`, or `work-blocked` for a blocked item) until the lease goes stale. What is recorded: the old lease is `superseded` and names `superseded_by`, the new one names `takeover_of`, and a `lease.taken-over` item event names the previous principal, run, last heartbeat and TTL.
3. **A refused completion is kept.** Every `rejected` outcome report is committed first and refused second, and a replay of its key refuses the same way. The payload stays on the item as evidence, visible through `work.lease.read-v1` and the `lease.outcome-reported` event, and the item does not change. The reason codes:
   - `lease-superseded`: the late completion after a takeover; this is the dead-lease code.
   - `lease-expired`: the lease went stale and nobody took it.
   - `lease-ended`: the lease was already settled or released.
   - `work-not-active`: the item was moved off `active`.
   - `work-blocked`: a blocker was added and is unsettled.
   - `verification-unsatisfied` (422); the others are 409.

   The lease checks come first, for either outcome, so a failed report on a dead lease is `rejected` too. `work-blocked` applies only to a succeeded outcome. A report on someone else's lease is `lease-not-found` (404) and stores nothing.
4. **`report_outcome` reports; the owner settles.** A succeeded report settles only if it meets the verification bar.
   - **Where the bar comes from.** The acceptance contracts of the item's releases at its current revision: `verification_profile`, default `checked`; `evidence_obligations` are the required checks. The bar in force when the lease was taken is pinned on the lease, and the pinned and current bars both apply, so no later reservation can lower it. From the sprintctl release after 0.9.0 (agentops#2539), a profile is a set of requirements, not a rank: `self-reported` needs nothing, `checked` needs `checks`, `role-separated` needs `checks` and `verifier-role`, `identity-separated` needs `checks` and `verifier-identity`, and `human-authorized` needs `human-authorization`. Bars combine by the union of their requirements and required checks, not strictest-wins, and a combined bar that no single profile names is reported as the names joined by `+` (for example `checked+human-authorized`) with its `requirements` listed.
   - **Which profiles settle.** `self-reported` settles on success. `checked` needs at least one reported check, all of them passed, and every required check present. From agentops#2539, a failed check or a missing required check rejects the report under every profile, `self-reported` included.
   - **Which profiles are refused.** From agentops#2539, sprintctl refuses `role-separated`, `identity-separated` and `human-authorized` when a contract is written, because only `checks` can be evaluated from the holder's own report and no decision path checks a verifier role, a verifier identity or a human yet. It also refuses `self-reported` together with `evidence_obligations`, and any unknown profile name. They do not wait for a verifier. A contract stored under 0.9.0 that names one of the three, or a malformed stored value (which counts as `human-authorized`), fails closed: the item cannot be leased (`verification-unsupported`). A lease already pinned under one by 0.9.0 leaves its report `awaiting-verification`, and nobody can claim the item until a decision lands (`work-awaiting-verification`). An `accept` still closes the item. That decision is stamped on the report, and an `accept` never marks such a report `settled`: it becomes `rejected` with `decided-accept-unverified`. Under 0.9.0 these three profiles waited (`awaiting-verification`), and any `accept` settled them without checking the verifier.
   - **The settlement record.** An `accept` decision attributed to `sprintctl:lease-settlement`. Its rationale begins "accepted under verification profile <profile>", then names the lease, holder, run and passed checks, and it cites the report's payload digest as evidence.
   - **Failure.** A failed outcome on a live lease is `recorded` and releases the lease.
   - **The edge** relays the report and the owner's answer, and never settles.
5. **Resume by the same principal: yes.**
   - **What a resume must repeat.** The same binding (principal, workspace, OAuth client and grant) and the same arguments under the same key: `item_id`, `run_id`, and `ttl_seconds`, including whether it was sent at all, because the digest covers the raw arguments.
   - **What else happens.**
     - Changing any argument, `run_id` included, is `idempotency-conflict`.
     - A run that is not the caller's (another principal or grant) is `run-not-found`.
   - **What the owner does with a resume** (`resumed: true`):
     - a fresh lease is the same lease with a refreshed heartbeat;
     - its own stale lease that nobody took is reclaimed under a new lease id that names the old one in `takeover_of`, and the reclaim is refused as a fresh claim would be (`maintenance-active`, `work-blocked`);
     - an active lease on an item no longer `active` is `work-not-active`, as on heartbeat;
     - a lease taken over is `lease-superseded`;
     - a settled or released lease comes back unchanged.
   - The run stays the same throughout, and at most one settlement is recorded.
6. **Ledger protocol.** `(workspace_id, principal_id, tool, key)`, per the section 5 amendment above. `claim_work` and `report_outcome` use the owner's ledger.
7. **No `parked` lease state.** TS-1's E4 clause says: on a rate-limit denial, "records the `rate_limit_event` as evidence, releases the lease, parks the claim".
   - **What the worker does.** It calls `report_outcome` with `outcome: failed` and the denial in the payload (and on its run, as M3-7's `rate_limit_event`).
   - **What the owner does.** It keeps the report and releases the lease (`recorded`). The item stays active for the next claim, by any family the harness chooses.
   - **What "parked" means.** It is that recorded report, an observation, not a lease state and not something the owner later resumes.
   - **A worker that cannot call.** Its lease simply goes stale.

Error codes the edge passes through unchanged: `lease-held`, `lease-superseded`, `lease-expired`, `lease-ended`, `lease-not-found`, `work-not-found`, `work-settled`, `work-blocked`, `work-not-active`, `maintenance-active`, `verification-unsatisfied`, `run-not-found`, `idempotency-conflict`, `invalid-arguments`.

**The edge's `claim_tools.py`** is built against these operations after vuoro#134 (agentops#2519 replay protection) merges, because #134 changes the same edge paths. Its rules:
- it resolves the caller's `run_id` first, the same way `record_tools.py` does;
- it forwards the caller's assertion and holds no credential;
- it lists nothing without a durable backend;
- it runs `lease.py`'s contract cases (expiry with heartbeat, a dead superseded id, a holder mismatch, a replayed completion on a dead lease) against both the in-memory `LeaseStore` and the durable operations. The error mapping:
  - `LeaseConflictError` = `lease-held`.
  - `LeaseHolderMismatchError` = `lease-not-found`.
  - `LeaseNotCurrentError` = `lease-superseded`, `lease-expired` or `lease-ended` for the holder, or `lease-not-found` for an unknown id and for a stranger presenting a superseded id.

  The owner deliberately tells the holder why its lease is dead, where `lease.py` gives one answer. That is safe because only the verified holder ever sees the distinction. `work-not-active` and `work-settled` have no `lease.py` equivalent. The expiry boundary differs by one tick: `lease.py` expires when `now > hb + ttl`, and the owner when `hb + ttl <= now`. The parametrized tests must not probe the exact instant.

**Amendment (2026-09-27, agentops#2540): the operator's lease contract.** The operator's lease semantics (agentops `docs/plans/2026-09-27-backlog-ideation.md`, R4) are normative. The sprintctl release after 0.9.0 aligns the lease with them, in place on the `-v1` operations because no client consumed them yet. Decisions 1, 2, 3 and 5 above describe 0.9.0. After that release:
- **Timing (semantics 1).** The TTL is authority configuration: 600 s by default, set by `SPRINTCTL_LEASE_TTL_SECONDS` (clamped to 30-3600; a malformed value falls back to 600 s, not 300 s as in Decision 1). A claim can no longer name `ttl_seconds`, and doing so is `invalid-arguments`. Every lease advertises `heartbeat_interval_seconds`, a fifth of its TTL (120 s by default).
- **Takeover (semantics 2).** The takeover event is `work.claim.taken-over`. It names `previous_claim_id`, `previous_principal`, `previous_generation`, `previous_last_heartbeat`, `new_claim_id`, `new_principal` and `reason: stale-lease`. Each lease carries a `generation`: its position among the item's leases, starting at 1. A claim is a lease, and `claim_id` is the `lease_id`.
- **Stale completion (semantics 3).** `lease-superseded` becomes `claim-superseded`, the published kebab-case form of the contract's `CLAIM_SUPERSEDED`. The rejection carries `details: {claim_id, current_generation, reported_generation}` wherever the service's rejection type can carry details. The released vuoro-service (0.1.77) cannot yet, so until it can, the generations appear in the message and in `work.lease.read-v1`. A report under a stale lease that nobody took over is still refused (`lease-expired`) and retained, because INV-L1 names stale leases too. Its holder reactivates the claim (semantics 5) and reports again.
- **Report outcome (semantics 4).** `work.lease.report-outcome-v1` is the operation. `work.lease.complete-v1` remains as its deprecated alias, with the same input, result and ledger. Results gain `settlement_effect` (`settled`, `lease-released`, `awaiting-verification` or `none`). The edge tool becomes `report_outcome`. The edge calls `work.lease.report-outcome-v1`, never the alias (vuoro-mcp-edge `claim_tools.py`, agentops#2520).
- **Same-principal restart (semantics 5).** Re-presenting the same claim (same binding, key and arguments) against one's own stale lease that nobody took over reactivates the same lease in place, with the same id and generation. It does not reclaim under a new id. Re-presenting a claim that was taken over is `claim-superseded`. A new key from the same binding releases the holder's old stale lease (`replaced-by-holder`) and does not supersede it.
- **`lease.py` (INV-L1).** The in-memory reference store retains a refused late completion from the lease's own holder as a non-settling outcome (`disposition="stale"`, `settlement_effect="none"`, `LeaseStore.retained_outcomes`) instead of discarding it. In the error mapping above, `claim-superseded` replaces `lease-superseded` (in `LeaseNotCurrentError` and in the pass-through list), and the pass-through list gains `work-awaiting-verification` and `verification-unsupported` (agentops#2539). A retried completion of a lease its holder already completed is refused the same way and retains nothing.
- **Catalog pins.** These changes and the agentops#2539 profile schema (`verification.profile` becomes a pattern and `requirements` becomes required) change the work adapter's catalog digest. The release-pin PR must re-pin `scripts/validate_released_work_adapter.py` `_EXPECTED_WORK_METADATA_SHA256` and `scripts/validate_released_catalog_composition.py` `EXPECTED_REVISION`.

`vuoro:work.claim` is granted only after every tenant runtime serves those tools.

## 7. Effect intents (E3)

**`propose_effect` accepts diff-shaped intents only:**

```
{run_id, repository, base_commit (40-hex), title (<= 200), rationale (<= 4000),
 unified_diff (<= 256 KiB, UTF-8 text), idempotency_key}
```

**It refuses, at proposal time:**
- binary patches, file-mode changes, symlinks and submodules;
- paths outside the repository;
- renames or deletes outside the repository's path allowlist;
- CI workflow paths (`.github/workflows/**`, `.forgejo/workflows/**`) and any file that `.sops.yaml` matches, unless the allowlist names them;
- a diff that does not apply to `base_commit`. This is checked by the reconciler before it acts, not by the edge, because the edge has no checkout.

**The public surface never executes anything.**
- `propose_effect` records an intent and returns `{intent_id, state: "proposed"}`. `get_effect` reports its state, which is one of `proposed`, `accepted`, `rejected`, `applied` or `failed`.
- There is no code path from the edge to an executor. Enforcement is by construction: the edge process has no credential and no route to one.

**The reconciler is a new, location-independent package.**
- It polls intents through an `IntentSource` protocol, applies the diff to a clean checkout at `base_commit`, and commits.
- The commit is signed by the reconciler's own key, injected at runtime; tests use a throwaway key.
- It pushes a branch and opens a PR through a `ProviderClient` protocol, restricted to a repository allowlist. It never merges and never pushes to a protected branch.
- Commit trailers `Vuoro-Run: <run_id>` and `Vuoro-Intent: <intent_id>` link each artifact to its run record.
- Where it runs is decided by the trusted-service boundary design (agentops `docs/plans/2026-09-26-trusted-service-boundary-design.md`, adopted pending review). E3 builds the package and its tests only: no manifests, credentials or deployment.

## 8. Merge order and handover

1. This seam PR.
2. E2 and E3 on independent branches (`e2/*`, `e3/*`) in bayleafwalker/vuoro. Their vuoro-cloud changes are pushed as branches on the GitHub replica, marked "carry to Forgejo".
3. E2 merges first. E3 rebases: expected conflicts are only the vuoro-cloud scope-table block and none in vuoro.
4. A local session carries the vuoro-cloud branches to Forgejo and does the releases, runtime pin, scope grants and promotion.
