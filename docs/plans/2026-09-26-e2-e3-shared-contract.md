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
| `vuoro:work.claim` | `work:claim` | `coordinate` | `claim_work`, `heartbeat`, `complete_work` | E2 |
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
- `idempotency.py`'s `IdempotencyLedger` protocol and `InMemoryIdempotencyLedger` still take `workspace_id` only. Until a follow-up changes that shared protocol, a ledger built on it must fold the principal into what it stores and looks up.

## 6. Claims (E2)

**Lease semantics.** `vuoro_service/lease.py` (the E0 `LeaseStore`) is the behaviour spec:
- expiry with heartbeat;
- the lease id, not the holder, is what counts as "current";
- a superseded lease id is permanently dead;
- a heartbeat or completion replayed against a dead lease fails even when it comes from the former holder.

**What it lacks.** The store is in memory only, so it is not the durable owner.

**E2 builds** `claim_work`, `heartbeat` and `complete_work` on a durable implementation of that same contract.
- The owner must respect `13-REPO-OWNERSHIP-AND-CHANGE-MATRIX.md`: sprintctl owns work semantics. So the durable lease is either sprintctl's claim machinery exposed as a public work contract, or a durable `LeaseStore` behind the shell that sprintctl's item state defers to.
- It is validated by `lease.py`'s contract tests, parametrized over both implementations.
- The (handle, auth_context) pair is validated as a whole: the lease holder is the run's `RunBinding`, not just the token.
- The advisory `reserve_work` is a separately named operation and is not part of E2.

**If the durable owner can't be built inside E2's branch,** E2 keeps `vuoro:work.claim` known but not granted. It ships run and evidence recording only, plus a short design note naming exactly what the lease owner must provide.

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
