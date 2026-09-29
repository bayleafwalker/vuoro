# M1-4: the differentiated settlement scenario over the public surface

**Item:** agentops#2524 (M1-4). It depends on agentops#2520: the exclusive durable lease in sprintctl 0.10.0 and the edge claim tools in vuoro-mcp-edge 0.1.6.
**Target:** vuoro-cloud `19-PRODUCT-POSITIONING-AND-PROOF.md`, "The intended differentiated scenario" (steps 1-8), which is marked as "a target conformance scenario, not a claim".
**Harness:** [`scripts/settlement_scenario.py`](../../../scripts/settlement_scenario.py). It re-runs green, and CI runs it on every PR (job `settlement-scenario`).
**Callers:** the item names "two hosted callers (Routines or `--cloud` sessions)". The parent session decided that the scripted callers here count as the hosted callers: they use the same public route and the same OAuth client as the hosted connector, and unlike a Routine they can be killed on cue and re-run. The decision is recorded as sprintctl note #3723 on agentops#2524.

| Run | Where | Result |
|---|---|---|
| [`local/`](local/transcript.md) (2026-09-29) | Real MCP edge + real runtime shell + pinned sprintctl 0.10.0 wheel on PostgreSQL 16, loopback, lease TTL 30 s | **GREEN**, 34/34 expectations |
| live, kotona on `api.vuoro.cloud` | Not run yet: it needs two OAuth consents and a short-lived PAT from the operator (see [Live run](#live-run)) | not run |

This packet makes no claim about the hosted workspace until the live run's transcript sits next to `local/`. The landing page may cite the scenario only after that (item's definition of done).

## What runs

The harness starts two callers, **A** and **B**, each as its own OS process. Each caller has its own lease binding: principal, workspace, OAuth client `claude-connector` and grant. They use only the public MCP tools: `register_run`, `claim_work`, `heartbeat`, `report_outcome` and `list_ready_work`. Because each caller is a separate process, "A is killed" is a real `SIGKILL` of A's process, not a skipped call.

An **authority reader** does two jobs over `POST /api/invoke/v1`:
- it creates the disposable items;
- it reads what the work owner recorded: `work.lease.read-v1`, `work.read.item-decisions`, `work.read.next-work` and `work.read.item`.

Every call and every answer is in `transcript.json`, and `transcript.md` renders the same entries in order.

In local mode a local Ed25519 key stands in for the gateway. It mints one assertion per request, in the same shape the gateway's OAuth `/mcp` path mints: `client_id` and `grant_id` set, and a fresh `jti` each time. The edge verifies the assertion as the first verifier, then forwards it to the shell with an edge proof, exactly as in a tenant pod. The shell and the edge are the shipped `create_app` and `create_edge_app` with the shipped toolsets (`vuoro_mcp_edge.composition.build_toolsets`), served by uvicorn. The work owner is the pinned sprintctl wheel's `register_work_catalog` over `WorkApplication.postgres`.

Disposable items are created in a sprint named `m1-4-scenario-<stamp>` on track `m1-4-scenario`. Their titles end "disposable, not real work". Cleanup runs even when setup or a case fails part way. It withdraws any item that did not settle (`work.decision.record` `withdraw`), then closes the sprint with the owner's `sprint.close` command through `work.lifecycle.arbitrate`. Both steps are recorded as expectations.

**What the transcript keeps.** Every work-item listing (`list_ready_work`, `next-work`) is cut down to the items the run created before it is written. What was dropped is kept only as a count (`omitted_foreign_items`), never as ids or titles, and `tests/test_settlement_scenario.py` pins this. The expectations read the owner's full answer, not the trimmed copy. The transcript does keep `principal_id` values and run, lease and grant ids. These are opaque identifiers, not credentials. On a live run they identify the operator's control-plane user, which is why the review step below applies.

## Acceptance, case by case

Each row names the expectation in the transcript that checks it. Every expectation passed in `local/`.

### takeover (items X and Y, where Y is blocked by X)

| Scenario step | What the owner returned | Expectation |
|---|---|---|
| 1. X becomes ready | `next-work` `ready_items` holds X and not Y | "X is ready and Y is not before anything happens" |
| 2. A reserves X | `claim_work`: generation 1, TTL 30 s, `heartbeat_interval_seconds` 6, `verification.profile` `checked`. A then heartbeats | "A holds generation 1 of X's lease" |
| 3. A disappears | A's process gets `SIGKILL` (`process: A killed`) | — |
| (exclusivity) | B's claim while A's lease is still fresh gets `lease-held`: "item #1 is leased; its lease becomes stale at … unless its holder heartbeats" | "B's claim is refused lease-held while A's lease is fresh" |
| 4. B takes over | The owner's clock marks A's lease `stale`. B's next claim gets generation 2, `took_over` = A's lease, `takeover_of` = A's lease. The owner's records show A's lease `superseded`, `superseded_by` = B's lease | "B's claim takes A's lease over", "B's lease is generation 2", "the owner's lease records name the takeover both ways" |
| 5. A reports success late | A new A process on the same grant gets `claim-superseded` on `heartbeat`. It gets `claim-superseded` again on `report_outcome`: "…the item's claim generation is now 2; the outcome was retained as evidence (report outcome_…) and nothing was settled" | "A's late heartbeat is refused claim-superseded", "A's late report_outcome is refused claim-superseded" |
| 6. A's result is retained, not settled | `work.lease.read-v1` `outcome_reports` holds A's report with its payload (`…-takeover-A-payload`), `disposition: rejected`, `reason_code: claim-superseded` | "A's refused report is retained on X with its payload", "the retained report is disposition rejected, reason claim-superseded" |
| (not yet ready) | Y is absent from both `next-work` `ready_items` and `list_ready_work` | "Y is not in … before settlement" (×2) |
| 7. B passes the configured profile | `report_outcome` (succeeded, check `scenario-check` passed) returns `settlement_effect: settled` | "B's report settles X" |
| 7. The authority settles | `work.read.item-decisions`: exactly one `accept`, `actor: sprintctl:lease-settlement`, rationale "accepted under verification profile checked: lease … held by … reported success; checks passed: scenario-check", `evidence_digests` = B's `payload_digest`. X is `done`/`accepted` | "exactly one accept decision on X", "the decision reads \"accepted under verification profile checked\"" (the rationale must start with exactly `accepted under verification profile checked:`), "the decision is the authority's", "X's verification bar and B's pinned bar are the named profile checked", "X is done" |
| 8. Dependent Y becomes ready | Y appears in both `next-work` `ready_items` and `list_ready_work` | "Y is in … after settlement" (×2) |

### restart (item R)

A claims R and heartbeats, and its process is killed. A new process then starts under the same grant, with the same `register_run` and `claim_work` idempotency keys:

- It gets the same `run_id`.
- `claim_work` answers `resumed: true` with the same lease (generation 1, heartbeat refreshed).
- `report_outcome` settles R.
- The owner holds one lease and one report (`settled`), with no rejection and exactly one `accept` decision.

Expectations: "the restarted A gets the same run id", "… resumes the same lease (resumed: true)", "… report settles the item", "one lease, one report, nothing rejected", "exactly one settlement".

### stale-restart (item S)

This case is the same as restart, except the new process arrives after the owner already reports A's lease as `stale` and nobody took it over. The owner reactivates the lease in place:

- same lease id;
- generation 1;
- no `takeover_of` and no `superseded_by`.

The report then settles S with no rejection. This is the lease contract's "the holder's own stale lease, if nobody took it over, is reactivated in place" (sprintctl `docs/reference/vuoro-work-adapter.md`).

## Re-running

Local (needs Docker, or pass `--pg-url` for an existing disposable database):

```sh
uv sync --all-packages --all-extras
python scripts/fetch_pinned_adapters.py packages/vuoro-service/composition/adapter-pins.json dist/adapters
uv pip install --no-deps dist/adapters/sprintctl-*.whl && uv pip install 'psycopg[binary]>=3.1,<4'
uv run --no-sync python scripts/settlement_scenario.py run --out _artifacts/m1-4-settlement-scenario
```

The run takes about 90 s. It exits 0 only when every expectation passes, and writes `transcript.json` and `transcript.md` to `--out`.

## Live run

The live run uses the same script and the same expectations, pointed at `https://api.vuoro.cloud/mcp`. It needs credentials that only the operator can give, each from a browser signed in to https://vuoro.cloud as an owner of `kotona` (workspace `01M3ABJS1QW0Q6BNDCHP0DDTYF`):

1. **Two `claude-connector` grants, one per caller.** Every authorization-code exchange creates a new grant, so the two grants are distinct lease bindings even under the same GitHub principal. Only `claude-connector` may hold `vuoro:work.claim` (vuoro-cloud `oauth_scopes.SCOPE_CLIENT_RESTRICTIONS`), and control's authorization server offers no grant without a browser (only `authorization_code` + PKCE and `refresh_token`).
2. **A workspace PAT with `work:read`, `work:sprint` and `work:lifecycle`, valid for one hour.** The MCP surface cannot create items, and kotona's work database (`vuoro_ws_01m3abjs1qw0`) is not the homelab vuoro-shared, so X, Y, R and S have to be created by direct invoke. Do not use the restore drill's device-flow PAT, which is fixed at 30 days. Mint it from the browser session with `expires_in_seconds: 3600` (step 2 below).

Run it from a vuoro checkout on the workstation, after the local setup above. It takes about 30 minutes, because the tenant lease TTL is 600 s and the run waits it out twice.

**Credential files.** Keep them under `$XDG_RUNTIME_DIR` (a per-user tmpfs), never on disk. `shred` does not reliably erase data on a journaling or copy-on-write filesystem. A refresh also rewrites the grant file through a temporary file and a rename, so earlier token versions would be left behind unshredded. The script creates every credential file 0600 from the start. The real protection is step 5: revoke everything on the server.

**0. Precondition.** `curl -s https://api.vuoro.cloud/.well-known/oauth-authorization-server | jq .scopes_supported` lists `vuoro:work.claim`. It did on 2026-09-29.

**1. Two grants.** For each `oauth-login`, open the printed URL, choose workspace `kotona`, and click Allow. Each run prints its `grant_id`; note both.

```sh
cd /projects/dev/vuoro   # or a worktree at this PR's head
S=scripts/settlement_scenario.py
T=$(mktemp -d "$XDG_RUNTIME_DIR/m14.XXXX")   # 0700, tmpfs
uv run --no-sync python $S oauth-login --token $T/a.json
uv run --no-sync python $S oauth-login --token $T/b.json
```

**2. A one-hour PAT.** In the browser, on https://vuoro.cloud, open the developer console and run:

```js
const csrf = document.cookie.match(/(?:^|; )vuoro_csrf=([^;]+)/)[1];
const api = "https://api.vuoro.cloud/api/control/v1";
const me = await (await fetch(`${api}/auth/session`, {credentials: "include"})).json();
const pat = await (await fetch(`${api}/workspaces/01M3ABJS1QW0Q6BNDCHP0DDTYF/tokens`, {
  method: "POST", credentials: "include",
  headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
  body: JSON.stringify({actor: me.actor, authorities: ["work:read", "work:sprint", "work:lifecycle"],
                        repo_ids: ["vuoro"], expires_in_seconds: 3600}),
})).json();
console.log(pat.token_id, pat.expires_at); copy(pat.token);   // the token is shown once
```

Paste the token into the PAT file without echoing it, and note `token_id`:

```sh
( umask 077; cat > $T/pat )   # paste, then Enter and Ctrl-D
```

**3. Run.**

```sh
uv run --no-sync python $S run --mode live --ttl 600 --token-a $T/a.json --token-b $T/b.json \
  --pat-file $T/pat --out docs/evidence/2026-09-29-m1-4-settlement-scenario/live
```

The run should end with `GREEN: evidence in …/live`. Cleanup withdraws Y and closes the `m1-4-scenario-<stamp>` sprint. X, R and S are left settled.

**4. Review before commit (mandatory).** Read `live/transcript.md` in full before `git add`:

- **Only the run's items.** No work item other than the run's own may appear. `omitted_foreign_items` counts are expected.
- **No secrets.** `grep -n -i -E 'bearer|vuo_pat|vuo_rt|eyJ' live/transcript.*` must print nothing.
- **Identifiers are acceptable.** `principal_id` (the operator's control-plane user id), grant and run ids are fine to publish. If they are not, stop and do not commit.

**5. Revoke everything.** Do this even if the run failed. In the same browser console:

```js
const ws = `${api}/workspaces/01M3ABJS1QW0Q6BNDCHP0DDTYF`;
const del = (path) => fetch(`${ws}/${path}`, {method: "DELETE", credentials: "include",
                                              headers: {"X-CSRF-Token": csrf}}).then((r) => r.status);
await del("grants/<grant_id A>"); await del("grants/<grant_id B>");   // 204 each
await del("tokens/<token_id>");                                        // 204
(await (await fetch(`${ws}/grants`, {credentials: "include"})).json())   // both show revoked_at
```

Then remove the local files: `rm -rf "$T"` (tmpfs, so nothing reaches the disk). Revoked grants stop working at the gateway at once (vuoro-cloud `docs/GETTING-STARTED.md`, "Revoking access"). The PAT would also lapse on its own after one hour.

**6. Send back.** Commit `live/`, link it from the table at the top, and add a note on agentops#2524.
