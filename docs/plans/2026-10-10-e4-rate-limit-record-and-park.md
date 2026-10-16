---
doc_id: vuoro-e4-rate-limit-record-and-park
title: "E4: rate-limit evidence and parking (records and parks, never routes)"
purpose: specification
lifecycle: proposed
effective: 2026-10-10
applies_to:
  components: [vuoro-core]
---

# E4: rate-limit evidence and parking

Served item: agentops#2472 ("E4: reactive quota-failover, records and parks").
Consumer side of agentops#2543 (sprintctl `work.parked` disposition, shipped
in sprintctl 0.18.0). This is a design and contract document; it adds no code.

## 1. Problem

When a plan-level or model-family-level rate limit stops a session, the work
it held is lost to the record: the lease times out, the claim looks
abandoned, and nothing says when or why it stalled. The edge plan
(`docs/plans/2026-09-20-vuoro-at-the-edge.md:191,266`) asks the substrate to
record the limit as evidence, release the lease and park the claim, and to
record which model family the next attempt used.

Three constraints fix the shape:

- **TS-1 / TS-2.** Vuoro is not a runner, queue, model router or worker
  supervisor; model choice stays native to the harness. A substrate that
  re-dispatched across families "would be a model router" (edge plan :191).
- **No predictive read exists.** "There is no supported programmatic read of
  individual plan consumption, on either vendor" (edge plan :174). The
  stream-json `rate_limit_event` is status only: allowed/denied, reset time,
  overage, no percentages (:181).
- **The owner already ships parking.** sprintctl's `report_outcome(failed,
  disposition=parked, reason_ref)` releases the lease
  (`end_reason=reported-parked`), appends `work.parked` and makes the item
  unclaimable (`work-parked`, 409) and not ready until a release to pending
  supersedes it (`sprintctl:docs/reference/vuoro-work-adapter.md:310-336`).

What is missing is on the Vuoro side. `vuoro-mcp-edge`'s `report_outcome`
parser refuses every argument outside its fixed set
(`packages/vuoro-mcp-edge/src/vuoro_mcp_edge/claim_tools.py:404-408`), so
`disposition` and `reason_ref` cannot reach the owner. The `claim_work`
description lists no `work-parked` refusal (:227-229). No evidence kind
records a limit; `rate_limit_event` appears only as an example in the
`report_outcome` payload description (:309).

## 2. Decision

1. **Record.** A worker that hits a limit appends one evidence entry with
   `kind: "rate_limit_event"` to its own run's chain through the existing
   `append_evidence` tool, then reports. The entry carries what the harness
   observed and nothing derived from consumption percentages (§4.1).
2. **Park through the owner.** The edge's `report_outcome` passes
   `disposition` and `reason_ref` through to the owner unchanged. For a rate
   limit, the worker reports `outcome: failed, disposition: parked,
   reason_ref: <evidence ref of the rate_limit_event>`. The owner releases the
   lease and parks the item. Vuoro adds no parked state of its own; the
   owner's `work.parked` event and `work.lease.read-v1.parked` field are the
   only parking record.
3. **Cheap check of done work at the edge.** When `reason_ref` uses the
   `vuoro-evidence:` form, the edge confirms it resolves to an entry in the
   reporting run's chain whose `kind` is `rate_limit_event`. A ref that does
   not resolve is refused (`reason-ref-unresolved`). This is a lookup against
   a record that already exists, not an approval step.
4. **Un-parking is someone else's act, triggered by recorded facts.** Vuoro
   runs no timer and never releases a parked item on its own (TS-1). Any
   coordinator or dispatcher may release the item to pending once the
   recorded `resets_at` has passed, using the owner's existing
   release-to-pending operation with a reason that cites the evidence ref.
   Vuoro offers a derived read (`refillable`, §4.4) so that check costs one
   query.
5. **Observe, never choose, the next family.** When a run claims an item
   whose newest parking cites a `rate_limit_event`, the edge appends a
   `parked_reclaim` observation to the claiming run's chain naming the parked
   event, the predecessor run and both runs' `model_id` and classified
   family. The family is classified from the `model_id` the harness declared
   at `register_run`; it is never an input to any decision.
6. **Plan-level and family-level limits are handled identically.** The
   difference is recorded (`limit_scope`) because it explains why a re-claim
   with another family did or did not progress; it changes no behaviour.

## 3. Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Edge re-dispatches the parked work to another model family | Model routing; excluded by TS-1/TS-2 and by the 2026-09-20 reconciliation on this item. Rebuild Phase 3 (second driver) was killed for the same reason (`docs/plans/2026-09-19-agentic-pipeline-first-principles-rebuild.md:369,377`). |
| Predictive balancing from `claude_code.rate_limit.used_percent` (agentops `.claude-headroom.json` gauges) or `/api/oauth/usage` | Not a supported interface (edge plan :187); would make E4 claim prediction. The gauges stay observability-only; E4 code must not read them. |
| A Vuoro-side `parked` lease state or table | Duplicates the owner's disposition; sprintctl states "There is no `parked` lease state" (`vuoro-work-adapter.md:435-436`). Two records of the same fact would drift. |
| Keep e2-e3 Decision 7 ("parked" = a failed report with the denial in the payload) | Superseded by #2543: a failed report alone leaves the item claimable, so the next claim races the reset window and the record cannot tell a park from a failure. |
| Auto-release when `resets_at` passes, run by the edge | A scheduler. Vuoro "never assigns, schedules, retries, supervises or expires" (TS-1). The derived `refillable` read gives the same value without the substrate acting. |
| Hold the parked item until an operator confirms the window has rolled | An operator gate that buys nothing: `resets_at` is recorded, and a too-early release costs one more recorded denial. |
| Fail the claim outright on a limit | Loses progress and misreports a stall as a failure; the acceptance criteria forbid it. |

## 4. Contract

### 4.1 `rate_limit_event` evidence entry

Appended with the existing `append_evidence` (`record_tools.py:846-890`). No
new tool, no schema migration: `kind` is already a free string.

| Field | Value |
|---|---|
| `run_id` | The run that hit the limit (the lease holder). |
| `kind` | `rate_limit_event` (exact). |
| `ref` | Harness-local pointer to the observation, e.g. `transcript:<session_id>#<uuid>` or `stream-json:<session_id>#<seq>`. Referenced by digest only; the transcript does not cross. |
| `digest` | `sha256:` of the canonical JSON of `provenance.observation` below. |
| `collector` | The emitting component, e.g. `agentops/hooks/subagent-exit.sh@<rev>`. |
| `validity` | `{basis: "bounded", valid_from: <observed_at>, valid_until: <resets_at or observed_at+24h>}`. |
| `claims[]` | One entry: `{claim_type: "capability_unavailable", subject: "model:<model_id>", detail: "<limit_scope> rate limit"}`. |
| `idempotency_key` | `rate-limit:<run_id>:<observed_at>`. |
| `provenance.observation` | Object below. |

`provenance.observation` (all fields required; unknowns are explicit):

| Field | Type | Meaning |
|---|---|---|
| `schema` | `"vuoro-rate-limit-observation/v1"` | Version. |
| `observed_at` | RFC 3339 UTC | When the harness saw the denial. |
| `status` | `"rejected"` \| `"allowed_warning"` | As the harness reported it. Only `rejected` justifies parking. |
| `limit_scope` | `"plan"` \| `"family"` \| `"unknown"` | Whether the denial applies to the whole plan or one family. `unknown` is valid and parks the same way. |
| `model_id` | string | The model the run was using (matches the run's registered `model_id`). |
| `model_family` | string | Classified per §4.5. |
| `resets_at` | RFC 3339 UTC \| `null` | From `quotaLimits.resetsAt` or the stream-json reset time; `null` when not parseable. |
| `reset_source` | `"harness-structured"` \| `"unparsed-local-string"` \| `"absent"` | Mirrors agentops `subagent-exit.sh` `RESET_SOURCE`. |
| `http_status` | integer \| `null` | e.g. 429 or 529. |
| `overage` | boolean \| `null` | Stream-json overage flag when present. |

Forbidden in the observation and anywhere in E4 code: consumption
percentages, remaining-quota estimates, predicted reset of another family, a
recommended family.

### 4.2 `report_outcome` passthrough (vuoro-mcp-edge)

Accept two optional arguments, passed to the owner as received:

| Argument | Rule |
|---|---|
| `disposition` | Only `"parked"`. Owner enforces: requires `outcome: failed` and `reason_ref`. |
| `reason_ref` | 1-512 chars, no surrounding whitespace (owner rule). Rate-limit parks use `vuoro-evidence:<run_id>/seq/<chain_seq>` (§4.2.1). |

#### 4.2.1 Evidence reference form

`vuoro-evidence:<run_id>/seq/<chain_seq>`. `chain_seq` is the position the
owner assigned to the appended entry in that run's evidence chain. It is
returned in the `append_evidence` result's evidence item, and it is unique
per run.

Two other values were considered and rejected as the key:

- The entry's `digest` field (the sha256 of the observation, §4.1).
  Identical observations, such as a retried denial in the same second,
  would collide.
- The chain hash `entry_digest`. The edge computes it but never returns it
  to a caller (`record_tools.py:24-29`), so a worker could not cite it.

Edge-side check: if `reason_ref` starts with `vuoro-evidence:`, the named
entry must exist in the reporting run's chain with `kind:
rate_limit_event`; otherwise the edge refuses before calling the owner.

Errors (edge errors are new; owner errors are passed through unchanged):

| Code | HTTP | Raised by | When |
|---|---|---|---|
| `invalid-arguments` | 422 | owner | `disposition` without `failed`, or without `reason_ref`, or bad `reason_ref` shape. |
| `reason-ref-unresolved` | 422 | edge | `vuoro-evidence:` ref names no entry in this run's chain, or one whose kind is not `rate_limit_event`. |
| `reason-ref-foreign-run` | 422 | edge | The ref names another run's chain. |
| existing lease errors | as today | owner | Unchanged (stale lease, wrong run). |

A successful parked report returns the owner result unchanged:
`settlement_effect: parked`, `parked: {event_id, reason_ref}`.

### 4.3 `claim_work` refusal

Add `work-parked` (409) to the documented refusals. Its description states:
non-retryable for this item until a release to pending; a client must not
loop on it. The edge maps it through unchanged.

### 4.4 Derived read: `refillable`

A read-only projection over owner data and run evidence, exposed through the
existing work read surface (`describe_work` adds a `parked` block; a list
filter `parked=refillable` on `list_ready_work`'s sibling read is optional):

```json
{
  "parked": {
    "event_id": 4711,
    "reason_ref": "vuoro-evidence:run-…/sha256:…",
    "parked_at": "2026-10-10T12:00:00Z",
    "rate_limit": {"limit_scope": "family", "model_family": "claude-opus",
                   "resets_at": "2026-10-10T17:00:00Z"},
    "refillable": true
  }
}
```

`refillable` is `true` when `resets_at` is non-null and in the past, or when
`resets_at` is null and `parked_at` is more than 24 h ago. It is a fact for
callers, not a trigger: nothing in Vuoro acts on it.

### 4.5 Model-family classification

A static, versioned, published table `vuoro-model-family/v1` maps a
`model_id` prefix to a family label (`claude-opus-*` -> `claude-opus`,
`claude-sonnet-*` -> `claude-sonnet`, `claude-haiku-*` -> `claude-haiku`,
`gpt-*` -> `openai-gpt`, otherwise `unclassified`). It is used only to label
records. An `unclassified` family is recorded, never refused. Changing the
table is a code review, not a runtime switch.

### 4.6 `parked_reclaim` observation

A parked item cannot be claimed (`work-parked`, 409) until a release to
pending supersedes the newest `work.parked`. After that release,
`work.lease.read-v1.parked` is `null`. The trigger therefore reads
**history**, not the current parking.

The trigger fires on a successful `claim_work` for item I when I's event
history, read through the owner's item event read, shows all of the
following:

- an `item-released` (release to pending) event that is the newest release
  before this claim's lease began
- that release superseded a `work.parked` event, meaning the `work.parked`
  is the newest parking older than the release
- the `work.parked` event's `reason_ref` uses the `vuoro-evidence:` form and
  resolves to a `rate_limit_event`

If any of these is missing, nothing is recorded: the claim is an ordinary
claim. Only the most recent park-then-release pair is considered. When the
trigger fires, the edge appends to the claiming run's chain:

| Field | Value |
|---|---|
| `kind` | `parked_reclaim` |
| `claims[]` | `{claim_type: "observation", subject: "item:<item_id>"}` |
| `provenance.observation` | `{schema: "vuoro-parked-reclaim/v1", parked_event_id, parked_reason_ref, predecessor_run_id, predecessor_model_id, predecessor_model_family, model_id, model_family, same_family: bool}` |
| `idempotency_key` | `parked-reclaim:<lease_id>` |

If the append fails, the claim still stands and the failure is returned as a
warning on the claim result; recording never blocks work.

### 4.7 States (owner states; Vuoro adds none)

```text
active + leased --report(failed, parked, reason_ref)--> active + parked (not claimable, not ready)
active + parked --release to pending (reason cites evidence)--> pending (ready, claimable)
pending --claim--> active + leased        [edge appends parked_reclaim]
active + leased --report(failed, parked)--> active + parked   (again)
```

A `rejected` report parks nothing (owner INV-L1).

## 5. Verification plan

Automated, in `packages/vuoro-mcp-edge/tests` against a fake owner adapter
with the shipped 0.18.0 contract:

1. **Plan limit.** Append a `rate_limit_event` with `limit_scope: plan`, report
   `failed/parked` citing it: owner receives `disposition` and `reason_ref`;
   result `settlement_effect: parked`; lease ended `reported-parked`; next
   `claim_work` returns `work-parked` 409.
2. **Family limit.** Same with `limit_scope: family`; identical behaviour.
3. **Unresolved ref.** `reason_ref` naming a missing entry, an entry of another
   kind, or another run's entry: `reason-ref-unresolved` / `-foreign-run`, and
   the owner is not called.
4. **Re-claim by another family.** Seed the history as `work.parked` (citing
   a `rate_limit_event` from a `claude-opus-…` run) followed by
   `item-released` to pending. Claim from a run whose
   `model_id` is `claude-sonnet-…` after a parked `claude-opus-…` run: one
   `parked_reclaim` entry with both families and `same_family: false`; no
   second active lease exists (owner reports one lease). Negative cases:
   - a claim whose newest release did not supersede a parking records
     nothing
   - a parking whose `reason_ref` is not a `rate_limit_event` ref records
     nothing
5. **No selection path.** A static test asserts no module under
   `vuoro_mcp_edge` imports the family table outside the two record writers,
   and that no tool argument or return value names a recommended family.
6. **No prediction.** `git grep -n 'used_percent\|oauth/usage\|headroom'
   packages/` returns nothing. A wording check covers the shipped tool
   descriptions and their code strings: the `claim_work`, `report_outcome`
   and `append_evidence` descriptions in `packages/vuoro-mcp-edge/src`. They
   must not contain "failover", "route", "routing" or "predict".

   The check is scoped to shipped surfaces. Design records, this document
   included, are out of scope, because they have to name what they rule
   out.

Cold run: `uv run --package vuoro-mcp-edge --extra test pytest
packages/vuoro-mcp-edge/tests` and `uv run pytest`.

The falsifiable bet from the edge plan (:336) stands: if parked claims
routinely sit long enough to matter, the answer is spend, not prediction.
The `refillable` read and the parked-age of each `work.parked` give that
measurement directly.

## 6. Migration

1. Stale dependency corrected: E4's "Depends on: E2, rebuild Phase 3, 6"
   (edge plan :266) names a killed phase. E2 (agentops#2466) is done and the
   owner contract (#2543) is shipped; E4 has no other prerequisite.
2. `docs/plans/2026-09-26-e2-e3-shared-contract.md` Decision 7 ("no
   `parked` lease state … 'parked' means that recorded report") is superseded
   in part by this document once ratified. That page has no `doc_id` and its
   own rule requires a separate PR for any change to it, so the supersession
   note on that page lands as its own PR, not here. Existing failed reports carrying a
   denial in `payload` remain valid history; they are not rewritten.
3. Edge change ships behind no flag: the two new arguments are optional and
   the owner already accepts them. A tenant runtime whose pinned sprintctl is
   older than 0.18.0 returns the owner's `invalid-arguments`; the edge's
   compatibility gate should require sprintctl >= 0.18.0 for the parked
   passthrough.
4. agentops harness side (separate item, agentops repo): `subagent-exit.sh`
   already derives `usage-limit`, `resetsAt` and `RESET_SOURCE`; it gains the
   `append_evidence` + parked `report_outcome` calls when the session holds a
   lease. That change belongs to agentops, not here.
5. vuoro-cloud needs no change beyond the edge image it already proxies;
   scopes are unchanged (`report_outcome` stays `vuoro:work.claim`,
   `append_evidence` stays `vuoro:evidence.record`).
