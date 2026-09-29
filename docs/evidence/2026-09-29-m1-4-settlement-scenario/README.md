# M1-4: the differentiated settlement scenario over the public surface

**Item:** agentops#2524 (M1-4). It depends on agentops#2520: the exclusive durable lease in sprintctl 0.10.0 and the edge claim tools in vuoro-mcp-edge 0.1.6.
**Target:** vuoro-cloud `19-PRODUCT-POSITIONING-AND-PROOF.md`, "The intended differentiated scenario" (steps 1-8), which is marked as "a target conformance scenario, not a claim".
**Harness:** [`scripts/settlement_scenario.py`](../../../scripts/settlement_scenario.py). It re-runs green, and CI runs it on every PR (job `settlement-scenario`).

| Run | Where | Result |
|---|---|---|
| [`local/`](local/transcript.md) (2026-09-29) | Real MCP edge + real runtime shell + pinned sprintctl 0.10.0 wheel on PostgreSQL 16, loopback, lease TTL 30 s | **GREEN**, 32/32 expectations |
| live, kotona on `api.vuoro.cloud` | Not run yet: it needs three browser approvals by the operator (see [Live run](#live-run)) | not run |

This packet makes no claim about the hosted workspace until the live run's transcript sits next to `local/`. The landing page may cite the scenario only after that (item's definition of done).

## What runs

The harness starts two callers, **A** and **B**, each as its own OS process. Each caller has its own lease binding: principal, workspace, OAuth client `claude-connector` and grant. They use only the public MCP tools: `register_run`, `claim_work`, `heartbeat`, `report_outcome` and `list_ready_work`. Because each caller is a separate process, "A is killed" is a real `SIGKILL` of A's process, not a skipped call.

An **authority reader** does two jobs over `POST /api/invoke/v1`:
- it creates the disposable items;
- it reads what the work owner recorded: `work.lease.read-v1`, `work.read.item-decisions`, `work.read.next-work` and `work.read.item`.

Every call and every answer is in `transcript.json`, and `transcript.md` renders the same entries in order.

In local mode a local Ed25519 key stands in for the gateway. It mints one assertion per request, in the same shape the gateway's OAuth `/mcp` path mints: `client_id` and `grant_id` set, and a fresh `jti` each time. The edge verifies the assertion as the first verifier, then forwards it to the shell with an edge proof, exactly as in a tenant pod. The shell and the edge are the shipped `create_app` and `create_edge_app` with the shipped toolsets (`vuoro_mcp_edge.composition.build_toolsets`), served by uvicorn. The work owner is the pinned sprintctl wheel's `register_work_catalog` over `WorkApplication.postgres`.

Disposable items are created in a sprint named `m1-4-scenario-<stamp>` on track `m1-4-scenario`. Their titles end "disposable, not real work". At the end, any item that did not settle is withdrawn (`work.decision.record` `withdraw`).

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
| 5. A reports success late | A new A process on the same grant gets `claim-superseded` on `heartbeat`. It gets `claim-superseded` again on `report_outcome`: "…the item's claim generation is now 2; the outcome was retained as evidence (report outcome_…) and nothing was settled" | "A's late report_outcome is refused claim-superseded" |
| 6. A's result is retained, not settled | `work.lease.read-v1` `outcome_reports` holds A's report with its payload (`…-takeover-A-payload`), `disposition: rejected`, `reason_code: claim-superseded` | "A's refused report is retained on X with its payload", "the retained report is disposition rejected, reason claim-superseded" |
| (not yet ready) | Y is absent from both `next-work` `ready_items` and `list_ready_work` | "Y is not in … before settlement" (×2) |
| 7. B passes the configured profile | `report_outcome` (succeeded, check `scenario-check` passed) returns `settlement_effect: settled` | "B's report settles X" |
| 7. The authority settles | `work.read.item-decisions`: exactly one `accept`, `actor: sprintctl:lease-settlement`, rationale "accepted under verification profile checked: lease … held by … reported success; checks passed: scenario-check", `evidence_digests` = B's `payload_digest`. X is `done`/`accepted` | "exactly one accept decision on X", "the decision reads \"accepted under verification profile checked\"", "the decision is the authority's", "X's verification bar is the named profile checked", "X is done" |
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

The live run uses the same script and the same expectations, pointed at `https://api.vuoro.cloud/mcp`. It needs three things that only the operator can give, each a browser approval as an owner of `kotona`:

1. **Two `claude-connector` grants, one per caller.** Every authorization-code exchange creates a new grant, so the two grants are distinct lease bindings even under the same GitHub principal. Only `claude-connector` may hold `vuoro:work.claim` (vuoro-cloud `oauth_scopes.SCOPE_CLIENT_RESTRICTIONS`), and control's authorization server offers no grant without a browser (only `authorization_code` + PKCE and `refresh_token`).
2. **A workspace PAT with `work:sprint` and `work:lifecycle`.** The MCP surface cannot create items, and kotona's work database (`vuoro_ws_01m3abjs1qw0`) is not the homelab vuoro-shared, so X, Y, R and S have to be created by direct invoke. This follows the restore-drill runbook's "Workspace PAT" step (vuoro-cloud `docs/runbooks/restore-drill.md`).

Run it from a vuoro checkout on the workstation, after the local setup above. It takes about 30 minutes, because the tenant lease TTL is 600 s and the run waits it out twice:

```sh
cd /projects/dev/vuoro   # or a worktree at this PR's head
S=scripts/settlement_scenario.py; T=$(mktemp -d ~/.config/vuoro/m14.XXXX)
uv run --no-sync python $S oauth-login --token $T/a.json   # open the printed URL, workspace kotona, Allow
uv run --no-sync python $S oauth-login --token $T/b.json   # a second consent: a second grant
# PAT: follow restore-drill.md "Workspace PAT" (client_name m1-4-scenario,
# Workspace kotona, Repository ID vuoro) and write the token to $T/pat
uv run --no-sync python $S run --mode live --ttl 600 --token-a $T/a.json --token-b $T/b.json \
  --pat-file $T/pat --out docs/evidence/2026-09-29-m1-4-settlement-scenario/live
shred -u $T/*; rmdir $T
```

Before you start, check that `curl -s https://api.vuoro.cloud/.well-known/oauth-authorization-server | jq .scopes_supported` lists `vuoro:work.claim`. It did on 2026-09-29.

The run should end with `GREEN: evidence in …/live`. It leaves one settled X, R and S and a withdrawn Y in a sprint named `m1-4-scenario-<stamp>` in kotona. Commit `live/`, then link it from the table at the top.
