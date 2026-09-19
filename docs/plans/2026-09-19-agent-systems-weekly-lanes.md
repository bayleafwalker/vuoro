# Agent Systems Weekly lanes: triage of the 11 and 18 September digests

**Status:** Revision 2 (2026-09-19, 12:30 UTC synthesis run). Supersedes Revision 1 (07:20 UTC
triage). `current` means operative, not approved; this document changes by being superseded and
carries no approval gate.
**Date:** 2026-09-19
**Decides against:** `2026-08-22-long-term-direction.md` (§1.2 non-goals as narrowed by D1, §3,
§4.1, §5 ledger contracts, §8 experiment lifecycle, §11 "Explicitly deferred", §13),
`docs/direction/disposition-register.yaml` v3,
`/projects/dev/agentops/docs/plans/2026-09-17-target-state.md` (TS-1, TS-2, TS-3, TS-5, TS-6,
TS-12, TS-13)
**Inputs to this revision:** `docs/evidence/2026-09-19-weekly-lanes/deep-read.md` (10:30 UTC run,
paper evidence and prior art), `docs/plans/2026-09-19-weekly-lanes-slices.md` (10:30 UTC run,
code grounding and first slices), `agentops/docs/assessments/lane-loop/refine-2026-09-19T1056Z.md`
(sprintctl intake `#2432`-`#2436`)
**Backlog projection:** `docs/plans/2026-09-19-weekly-lanes-work-items.yaml`
**Evidence:** `docs/evidence/2026-09-19-weekly-lanes/papers/` (claims per paper; full texts not
committed, the repo is public)

## Revision 2: changelog

Decided from the goal state, not from current usage. Where the two 10:30 UTC runs disagreed with
Revision 1, the run with primary evidence won: the deep read for paper claims, the slices run for
what the code does.

### Disposition changes

| Lane | Rev 1 | Rev 2 | Decided by |
|---|---|---|---|
| W1-4 Necessary Tool-Evidence Paths | `fold_into_existing` (sprintctl, S-M) | **`defer_with_trigger`** (sprintctl). Trigger: a live-state count finds at least five sprint-559 items with ingested evidence refs. | Slices run §1: the step-0 producer count is new machinery with no pattern to reuse, and it cannot be measured from either repository, only from live sprintctl state. A lane whose first action is "measure, and below threshold stop" is a deferred lane under this document's own §4 vocabulary. TS-12 (measure producers before adding a declared contract) says the same. The deep read struck the paper's Terminal-Bench numbers as a digest conflation; the one surviving number (1.55 vs 5.36 calls per sample) comes from an RL reward term on a vision-language model and transfers conceptually, not empirically. The measurement itself is filed (WL-A2a); the schema change is not. |

The other eleven dispositions stand. The deep read found no lane whose paper evidence points to a
different tier, and no case where a mechanically verified quote was wrong. That is worth recording:
the Revision 1 source check held under an arms-length re-verification with different tooling
(GitHub clones and secondary corroboration in place of the archived full text).

### Coverage claims corrected (slices run, against sprintctl `7e86e23` and agentops `39f1bd3`)

1. **W1-2: four guard hooks, not five.** `secret-read-guard.sh` does not exist; it is named only in
   a comment in `bounded-read-guard.sh:22`. The slice instruments `bounded-read-guard.sh`,
   `nfs-workspace-guard.sh`, `forge-sandbox-guard.sh` and `gate-check.sh`. Building the fifth hook is
   unscoped work outside this plan.
2. **W1-2: the `decisions` partition in `log-session-cost.sh` does not exist.** Revision 1 described
   it as present. `gates-$SESSION.jsonl` is written by `gate-log.sh` (PostToolUse) as flat
   `{ts, cmd, exit, signal, ok}` rows, and `log-session-cost.sh:75-81` computes `rework_rounds` with no
   `kind` discriminator. The first decision row written would corrupt `rework_rounds` for every
   later session. The `kind != "decision"` filter is part of the same change as the helper, not a
   follow-up.
3. **W1-6: `review_required` is inert metadata**, written as `DEFAULT_ACCEPTANCE_CONTRACT`
   (`releases.py:32`) and never read or branched on. "Releases carrying the default contract key" is
   the correct phrasing. The alias-versus-explicit accept split has no stored provenance marker;
   the derivation uses `rationale == ""` as the alias-path proxy (the alias paths at
   `db.py:1598-1604` and `authority.py:283,314-324` write no rationale; `item decide` requires one).
4. **W1-1: `decisions.py:135-150` is `transition_error`**, the general terminal-state guard, not a
   supersede rule. No supersede-binds-to-terminal-Decision rule exists in any form. It is the
   nearest fold point for a rule that would be new; the disposition (trigger-gated) is unchanged.
5. **W2-4: "reuses Workstream D step 1" holds for the `rework_rounds >= 3` half only.** The
   `git diff base..head` test/gate-file weakening half has no precedent and is new code.
6. **TS-2 confirmation:** `hybrid_dispatch.py` is deleted (`0baa680`). Revision 1's clause that it
   "survives in the `base-main` snapshot and stale worktrees" is unverifiable from any checkout the
   runs had and is dropped. Two harmless textual survivals remain in `gate-log.sh:20`'s
   `GATE_PATTERN` and its test fixtures.
7. **Host items.** `#2057` and `#2058` were hybrid-dispatch qualification items, never a
   change-memory item; the agentops backlog reconciliation (agentops #182) retired both, and the
   10:41 UTC refine tick filed `#2432`-`#2436` in sprint 559 against this plan's workstreams.
   Workstream B and Workstream C step 2 ride `#2433`. Revision 1's "revision 2 names or files it"
   is closed by naming.

### Slices dropped

- Instrumenting `secret-read-guard.sh` (the hook does not exist).
- The `evidence_obligations` schema change as a 2026-09-20 slice (re-tiered above; only the
  measurement remains as work).
- `log-session-cost.sh` "partitions decisions" as an existing behaviour to rely on (it is now the
  required same-change fix in WL-D1).

### Evidence-text corrections applied to §5 (deep read)

W1-4 Terminal-Bench numbers struck; W2-2 reframed as a motivating failure case; W2-6 release
status updated; W2-3 caveat narrowed to the solve-count comparison; W1-5 sharpened to all three
judgment sets sharing one retrieval-pool origin; W2-4 corrected to a three-model study with the
57.2 %/73 % pair GLM 5.2-specific; W2-5 given the ERPBench name-collision flag and the paper's own
extremal wording.

### Sequencing changes

Workstreams are now ordered by what the 2026-09-20 06:00 UTC implementation run can take on which
host. The first experiment is unchanged (W1-3 rescore, `#2436`). Workstream D step 2 is explicitly
ordered after WL-D1 because it inherits the `rework_rounds` risk otherwise.

## Source and method

Two "Agent Systems Weekly" digests, 11 September 2026 (lanes W1-1..W1-6) and 18 September 2026
(W2-1..W2-6), twelve arXiv papers in total. Every digest item was treated as a candidate lane.

Revision 1 method, per lane: (1) source check of the digest's claims against the archived full text
(`full_html`, 2609.* arXiv ids in the table below); (2) grounding in code, every fold target
named by file and line; (3) a design with an §8.2 experiment; (4) two independent sceptic passes,
revisions applied; then one synthesis and one completeness critic over all twelve.

Revision 2 adds two independent runs and reconciles them: a deep read of paper evidence and prior
art (arXiv and most project pages were blocked by the egress proxy; GitHub clones of author repos
and project-page branches were the primary channel, WebSearch corroboration the secondary one), and
a repo-grounding run that re-checked every sprintctl and agentops file:line citation against
`7e86e23` and `39f1bd3`. `appservice`, `local-inference`, `vuoro-evidence` and `outctl` were not
available to either run; citations into them carry over from Revision 1 unverified and are marked.

Two ground rules from the goal state decided most dispositions. TS-2 retires hybrid dispatch
and OpenCode worker routing (`templates/dispatch` was deleted in S2 item 6), so nothing may fold
into `hybrid_dispatch.py` or a route qualification. §11 "Explicitly deferred" rules out a
Vuoro-owned queue, worker supervisor, model router or prompt platform, so nothing may add an
execution control plane. Everything kept lands as a derived projection or a reporting-layer
change over gates that already exist. A third rule, TS-12, decided the one re-tiering in this
revision: a declared contract is measured for producers before it is added.

## 1. Disposition table

| Lane | Disposition (rev 2) | Owner repo | Host item | First slice or fold target | Effort |
|---|---|---|---|---|---|
| W1-1 Emergent cheating and whistleblowing in research swarms (2609.04170) | defer_with_trigger (unchanged) | sprintctl | none | On trigger only: a new supersede rule binding append-only to the earlier terminal Decision, nearest fold point `decisions.py:135-150` (`transition_error`, a general terminal guard, not existing supersede logic); S4 derived `contested_accept`; downstream query only if depcore dependants exist outside the closure batch | S |
| W1-2 MMPIBench multimodal prompt injection (2609.09404) | run_now (narrowed, strengthened) | agentops | `#2434` | Decision rows at every PreToolUse deny/ask point in the four existing guard hooks (`bounded-read-guard.sh`, `nfs-workspace-guard.sh`, `forge-sandbox-guard.sh`, `gate-check.sh`) appended to `gates-$SESSION.jsonl`, with the `kind != "decision"` filter in `log-session-cost.sh` in the same change; carrier replay deferred | S |
| W1-3 WorldBench (2609.01056; real title "WorldBench: Culturally Grounded Benchmark for Multilingual Agents") | fold_into_existing (strengthened) | local-inference; sprintctl (second slice) | `#2436`; `#2433` | `outcome ∈ {completed, completed_with_collateral_change, incomplete}` derived from existing `accepted` and `violated_constraint` in `exp-tier-effect.py`, `analyse-tier-effect.py`, `results-to-scorecard.py`; second slice is the declared write scope on Release, see Workstream C | S |
| W1-4 Necessary Tool-Evidence Paths (2609.03493) | **defer_with_trigger** (was fold_into_existing) | sprintctl | none (measurement WL-A2a is filed by the next refine tick) | No schema change. One live-state read: count sprint-559 items with ingested evidence refs, recorded as a Decision. At five or more, the `evidence_obligations` key on `acceptance_contract` plus a derived `unmet_obligations()` report query activates; below five, the Decision records the deferral | S (measurement) |
| W1-5 Q2D-Web retrieval on agent-written queries (Perplexity, 2609.08887) | defer_with_trigger (caution strengthened) | agentops | none | No build. On trigger, one §8.2 probe under `_projects/retrieval-q2d-probe/` using acceptance-lab scorers, never Q2D-Web's own judgment sets as independent validation | S (deferred) |
| W1-6 From language models to world-acting systems (2609.04894) | fold_into_existing (narrowed, confirmed) | sprintctl | `#2432` | `accepted_without_evidence` as the fourth `CATEGORIES` entry in `sprintctl/sprintctl/unbound.py`; alias/explicit split by the `rationale == ""` proxy; route-level AgentProfile rejected | S |
| W2-1 Agora: Git as shared memory for collective autoresearch (2609.18094) | fold_into_existing (minimal, confirmed) | sprintctl | `#2433` | One scope-note line on `#2433`: log claim-level supersession/derivation/corroboration gaps as observations with `evidence_ref`; no code | S (near zero) |
| W2-2 Emergence World adversarial stress-testing (2609.17320) | reference, no build (reframed) | sprintctl (if ever) | none | No fold now. Motivating failure case for §5.1 EvidenceSet validity and expiry (the platform has no expiry or revocation at all, and tainted memory was acted on up to 46 h later). A `revoked` reason and a completion-time gate are rejected (no producer, wrong code path) | none |
| W2-3 SoL-Pi efficient harness design (2609.20519) | fold_into_existing (confirmed) | agentops | `#2435` | Relocate the context-economy instrument (`outctl/studies/context-economy/{measure,classes,waste}.py`) to `agentops/scripts/context_economy/`, measure the Phase 1/2 shares before any gate; add `verify_quotes.py` for arm (b); arm (a) rejected | S-M |
| W2-4 Monitoring reward hacking through internal representations (2609.19101) | fold_into_existing (narrowed, confirmed) | agentops | none (files after `#2434` lands) | `check_trajectory_flags.py` run locally at the verify stage: `rework_rounds >= 3` reuses `#2434`'s corrected computation; test/gate-file weakening from `git diff base..head` is new code; advisory flag triggers one extra Sonnet review-synthesis pass on the flagged unit only | S |
| W2-5 ERPBench state-grounded acceptance (2609.17885) | fold_into_existing (confirmed) | appservice | appservice-owned | Delivery unit F (off-cluster collector, expiring receipt, assessor) from `appservice` `2026-08-27-repository-assurance-and-operational-evidence.md`, line 16 "not started" as of Revision 1; agentops `_projects` ExperimentRecord compares accept basis with F's verdict after two collection cycles | M |
| W2-6 HazardAuditor execution-grounded safety across harnesses (2609.15134) | reject (unchanged; release caveat updated) | vuoro-evidence (if ever) | none | No build. `EFFECT_UNCERTAIN`, `GrantUse.UNCERTAIN_USE` and OBSERVATION reconciliation already exist; the gap is the dark evaluator (0 callers), an existing S4/S5 wiring item | none |

Host items are the sprint-559 intake of `agentops/docs/assessments/lane-loop/refine-2026-09-19T1056Z.md`
§3 (`#2432` fast-build, `#2433` frontier-plan blocked on S3 Release as ordering not gate, `#2434`
fast-build helper with coordinator for hook call sites, `#2435` fast-build, `#2436` local lane,
workstation-only). The 2026-09-20 06:00 UTC implementation run records Decisions on these ids and
files no new items for them. No trigger in §4 depends on an item id.

## 2. Workstreams in order

Order is build order for the 2026-09-20 implementation run, then the items that wait on it, then
the gated remainder. Three repos build in parallel (sprintctl, agentops, local-inference); the two
real cross-item orderings are WL-C2 after WL-C1's enum and WL-D2 after WL-D1's filter. Item ids
`WL-*` are the rows of `2026-09-19-weekly-lanes-work-items.yaml`.

### Step 1 (2026-09-20 06:00 UTC, devbox): three S slices, no dependency

1. **WL-A1 / `#2432`, Workstream A step 1 (sprintctl).** Add `accepted_without_evidence` to
   `unbound.py`'s `CATEGORIES` (today `legacy_done`, `decided_unreleased`, `released_undecided`).
   A finding for any item whose latest terminal Decision has `kind="accept"` and
   `evidence_digests == []` on a Release whose `acceptance_contract` carries the default
   `{"review_required": true}` key. Split by `rationale == ""` (alias path: `status done`/outbox)
   versus non-empty (explicit `item decide` with no `--evidence`). Reports, never blocks. Tests in
   `tests/test_releases.py`, `tests/test_decisions.py`, `tests/pg/test_releases.py`,
   `tests/test_served_decisions.py` (alias accepts happen on the served path too).
   Pre-merge check, not post: run the derivation against live sprint state; zero findings across
   every open and recently closed sprint means the alias path is not producing evidence-free
   accepts and the kind is retired before it ships.
2. **WL-D1 / `#2434`, Workstream D step 1 (agentops).** One shared shell function
   (`hooks/lib/emit-decision.sh` or equivalent) called at each deny/ask emit point of the four
   existing guard hooks, in addition to the stdout `hookSpecificOutput`, appending
   `{kind:"decision", ts, hook, rule_id, tool, policy_decision}` to `gates-$SESSION.jsonl`. In the
   same change: `log-session-cost.sh:75-81` filters `kind != "decision"` (or `select(.cmd != null)`)
   before `rework_rounds`, and the `gates` metadata published to auditctl either documents the mixed
   array or splits a sibling `decisions` key; one choice, documented in the commit. No OTel GenAI
   schema pin (the conventions are in Development status with no pinnable release). Tests:
   `hooks/tests/test-decision-row.sh` on the `test-gate-log.sh` REQ pattern, plus a
   `test-cost-hook-fields.sh` case proving `rework_rounds` is unchanged by interleaved decision rows
   on a replay fixture with failed-then-retried gate commands.
3. **WL-S1 / `#2435`, standalone context economy (agentops).** Relocate `measure.py`, `classes.py`,
   `waste.py` from `outctl/studies/context-economy/` unchanged (precondition `outctl` at `94840d7`
   on devbox, per the refine tick; not verifiable from the cloud runs) and run them against the
   current stack to get real Bash-share and file-read-share numbers before deciding whether the
   28 %/45 % thresholds are the right gates. Add `agentops/scripts/verify_quotes.py`: the delegated
   worker returns `{claim, quote, ref}`, the checker matches each quote verbatim
   (whitespace-normalised) against `path:Lstart-Lend` or a spool file plus sha256, and failures
   are shown as UNVERIFIED, never dropped. outctl stays retired; only the instrument moves.

### Step 2 (2026-09-20, workstation): the first experiment

4. **WL-C1 / `#2436`, Workstream C step 1 (local-inference).** The §3 ExperimentRecord. Offline
   rescore, zero model spend. Neither 10:30 run could re-ground the local-inference file:line
   citations; the implementer re-verifies `exp-tier-effect.py:158`, `results-to-scorecard.py:28-29`
   and `probe-escalation.sh` before editing.

### Step 3 (after step 1 lands)

5. **WL-D2, Workstream D step 2 (agentops, files after `#2434`).** `check_trajectory_flags.py`
   reads the session gate log plus `git diff base..head` and flags (a) test/gate-file weakening and
   (c) `rework_rounds >= 3` or gate-command churn. Runs locally at the verify stage, not in CI (no
   gate log on a runner). A flag triggers one extra Sonnet `review-synthesis` pass on the flagged
   unit; no Opus escalation, never rejects, never touches sprintctl state. Waits on WL-D1's filter or
   inherits the corruption. The scope-drift signal folds into WL-C2, not a separate build.
6. **WL-A2a, Workstream A step 2 measurement (sprintctl, live state).** One read against live
   sprintctl (`sprintctl sprint show 559` or equivalent): the number of sprint-559 items with
   ingested evidence refs. Recorded as a Decision with the count as rationale. This is the W1-4
   trigger check; nothing else in the lane runs before it. Below five, the same Decision records the
   deferral and the lane stays in §4.
7. **WL-C2 / `#2433`, Workstream C step 2 plus Workstream B (sprintctl, frontier-plan).** A
   declared write scope on `WorkRelease` (path prefixes or globs, populated at reserve time),
   specialising the "bounded context and source references" §4.1 already says a WorkRelease
   carries; not a new noun. At Decision time `collateral` = any changed path in the diff digest
   outside the declared scope, and the same three-way `outcome` enum as WL-C1 (adopted, not
   re-defined) lands on the Decision's evidence digest. Borrow AppWorld's table/row/column diff
   granularity rather than a "diff nonzero" check once a declared scope exists to diff against.
   The W2-1 change-memory scope note rides this item as one line. Vuoro projects `outcome` and
   `collateral` onto EvidenceSet/ExperimentRecord exports and never computes or adjudicates them.
   Ordering dependency on S3 Release is substantially satisfied (`ca3fa92`, `a3e02db`, `22f7afb`
   merged); condition to start is one real Release with a declared scope and a diff digest through
   ordinary use, not a synthetic fixture.

### Step 4 (external or trigger-gated)

8. **WL-C3 (appservice) and WL-C4 (agentops).** W2-5 Delivery unit F is appservice's own plan;
   agentops's `_projects` ExperimentRecord over F's output starts after two collection cycles.
9. **WL-A3, Workstream A step 3 (sprintctl, S4).** `decisions_on_expired_evidence`: port the
   vuoro-evidence reducer's `expiry_of()` and join `work_decision.evidence_digests` (today an
   existence check only, `db.py:1154-1155`, `pg.py:2409-2411`) to validity computed at read time.
   No stored expiry event, no new reason value, no gate. Waits for the S4 evidence-home migration.
10. **Trigger-gated remainder:** W1-4 schema change (WL-A2b), W1-1 contest machinery, W1-2 carrier
    replay, W1-5 probe, W2-2 `revoked` reason. Triggers in §4.

### Rejected outright

W2-6. The transferable idea (judge by execution outcome, not transcript) is already implemented
in vuoro-evidence. Building a canonical multi-harness event envelope would violate §3.2
(normalize edges, not interiors) and the dossier's permanent exclusion of tool-call telemetry;
MCP's stabilised Task object (spec 2026-07-28) already covers the correlation ground such an
envelope would duplicate.

## 3. First experiment

Pick unchanged: W1-3's local-inference outcome-field rescore on the escalation-probe data
(`#2436`). It needs no new agent run, has a known positive signal (both rows of
`local-inference/benchmarks/results/2026-08-19T120204-escalation.jsonl` are `accepted=true` and
`violated_constraint=true`, verified in Revision 1), touches no lifecycle gate, and is the one lane
where both sceptic passes converged on the same correction. The deep read rates its source the
best-evidenced of the twelve (36/36 quotes verified; CTS is a conjunctive metric with a quantified
failure taxonomy and human-audited judge agreement, κ 0.76-0.84). Every other candidate starts
from zero known occurrences.

```yaml
id: exp-2026-09-local-inference-outcome-collateral
host_item: "#2436"
owner_repo: local-inference
host: workstation (local-inference is workstation-only)
hypothesis: >
  Runs currently scored "accepted" include runs that changed state outside their
  declared or protected targets, and current reporting hides this because
  accepted = tests_pass regardless of violated_constraint. Splitting outcome into
  {completed, completed_with_collateral_change, incomplete} surfaces the two known
  tampered-and-accepted escalation rows, with task_id/arm attached so they enter
  per-arm aggregation.
baseline: >
  Current local-inference scoring. exp-tier-effect.py:158 sets accepted =
  tests_pass(dest); violated_constraint (git diff on tests/) is recorded next to it
  and never folded into acceptance. analyse-tier-effect.py counts r['accepted']
  only. results-to-scorecard.py:28-29 records a tamper as the free-text note
  "TAMPER: tests/ was modified by the agent". probe-escalation.sh records the
  tamper next to pass, also unfolded. (Citations from Revision 1; neither 10:30 UTC
  run had the repository. Re-verify before editing.)
challenger: >
  Pure function over existing booleans:
  outcome = 'incomplete' if not accepted
            else 'completed_with_collateral_change' if violated_constraint
            else 'completed'.
  Applied identically to exp-tier-effect.py output, probe-escalation.sh output and
  results-to-scorecard.py. Rows lacking violated_constraint get outcome = null and
  unavailable_reason = "no_violated_constraint_field". No new gates, no model spend.
  The enum is the one WL-C2 (#2433) adopts; it is defined here once.
task_sample: >
  All parseable rows in local-inference/benchmarks/results/*.jsonl, counted in
  Revision 1: 219 rows across 25 files (88 malformed lines skipped and reported).
  38 rows carry accepted=true: 2026-08-19-tier-effect.jsonl (18, 0 tampered),
  2026-08-19-worker-fast-failures.jsonl (18, 0 tampered) and
  2026-08-19T120204-escalation.jsonl (2, both tampered). 181 rows have no
  violated_constraint field. The escalation rows carry experiment_id and profile but
  no task_id/arm; both are backfilled from the run log, and rows without a
  recoverable task_id are reported as "unattributable".
measures:
  - outcome-class counts per source file and per arm/profile
  - previously invisible collateral rows now surfaced by class (target: the 2 known
    escalation rows, confirmed by the derived function)
  - rows with outcome derivable versus null with unavailable_reason
  - whether task-05's src/registry.py rule is a convention test (outside
    protected_paths) or a hard-gated invariant, per the second sceptic pass
  - malformed JSONL lines per file (88 in Revision 1), reported so the corpus can be
    repaired
  - whether local-inference has a test suite these scripts fall under (none named in
    Revision 1); if it does, the derivation gets a unit test there
falsifier: >
  Retire the outcome field if the rescore surfaces zero
  completed_with_collateral_change rows (the derivation is wrong), or if per-arm
  rankings are unchanged and no row was previously invisible under the TAMPER
  free-text convention (the field adds nothing a reader of scorecard.csv does not
  already have). Retiring it here also retires the enum from WL-C2 before that item
  starts.
rollback: >
  The field is additive: accepted and the TAMPER note are kept unchanged, the
  rescore writes a new scorecard beside the old one, and the schema change to
  probe-escalation.sh is one added key. Reverting is deleting the outcome column
  and the derivation function; no stored ledger row is created (the result lands
  as a Decision on #2436 with this record as change memory, per the dossier's
  noun-drop rule).
limitations: >
  violated_constraint is a boolean over tests/ only, so this slice cannot tell a
  collateral edit's location or size; AppWorld-style table/row/column granularity
  waits for a declared write scope (WL-C2). Two positive rows is a smoke signal,
  not a rate.
first_slice: >
  In /projects/dev/local-inference: (1) add outcome beside accepted in
  exp-tier-effect.py; (2) count by outcome in analyse-tier-effect.py's per-arm
  summary; (3) add an outcome column to results-to-scorecard.py, derived for
  existing records, TAMPER note kept; (4) add task_id/arm to probe-escalation.sh's
  JSONL and backfill the two rows; (5) rescore all results and record class counts
  as a Decision on #2436 with rationale.
effort: S
depends_on: []
```

## 4. Deferred lanes with triggers; rejected lanes with reasons

Triggers are observable conditions. None depends on a backlog item id.

| Lane / part | Trigger |
|---|---|
| W1-4 `evidence_obligations` key on `acceptance_contract` plus `unmet_obligations()` (WL-A2b) | WL-A2a's live-state Decision records five or more sprint-559 items with ingested evidence refs. Below five, the lane stays here and the count is re-read at the next sprint boundary, not before. |
| W1-4 per-call obligation reference, expected-gain stopping, OTel redundancy measurement | Both: (i) the goal state changes to permit a Vuoro-owned worker supervisor (§11 explicitly defers it), and (ii) emit-only harness traces with evidence-goal tagging exist in agentops (TS-15 allowlist checked in CI) and show redundant-call rates the post-hoc query cannot correct. |
| W1-1 full contest machinery (supersede rule, `contested_accept`, downstream query) | The S3 truth-audit quarter records a post-S3 accept Decision that independent evidence later contradicts, for example an auditctl observation with `confirms=false` referencing the Decision id. Zero cases today; sprint 545's 26 legacy re-marks do not count, no accept Decision exists for them to contest. |
| W1-2 carrier replay | Either (a) about two weeks of decision rows (from `#2434`) show denials whose origin the rows alone cannot explain (injection-like or false-positive pattern), or (b) a baseline policy Rego with tests exists and an observation-mode run over a full backlog has produced a denial set to replay against. Note `#2193` (author baseline Rego) was retired on 2026-09-19 under TS-1/TS-2, so (b) is further off than Revision 1 implied. |
| W1-5 Q2D-Web method (queries by phase, citation-derived labels, pooled top-k judging) | Any of: (1) an operator-owned retrieval surface is proposed (local retriever/index, or a search provider replacing WebSearch); (2) a context-compiler or handoff change alters retrieved evidence; (3) the direction's metric "evidence retrievals required after initial handoff" rises, or a handover attributes a wrong outcome to missing or stale retrieved evidence. When probed, score with acceptance-lab scorers; Q2D-Web's three judgment sets share one retrieval-pool origin and are not independent validation. |
| W2-1 full Agora comparison (diversity-aware selection, notes versus graph) | No numeric trigger exists; nothing detects duplicated open-ended investigations today. Reopen by a new lane proposal once sprintctl holds parallel open-ended investigations on one question. Dated recheck (WL-R1, 2026-10-31): a new arXiv version or repo commit of `yifanzhang-pro/Agora` adding the matched comparison the paper proposes but did not run. |
| W2-2 `revoked:<collector\|source>` reason and untrusted-ingest provenance | Either (i) a real producer is observed: a collector version found faulty, or a kctl lesson/claim import retracted after S4 (TS-12, TS-13); or (ii) one of our own runs shows untrusted content persisting into agent memory or state and being acted on later. If it fires, model revocation as justification-graph recomputation (JTMS), not a boolean flipped in place, and emit it as an event (RFC 7009 shape), matching §5.1's `GrantEscalated`. |
| W2-3 arm (b) trial beyond the extraction data point | WL-S1 has measured the Phase 1/2 shares and `verify_quotes.py` exists. Dated recheck (WL-R1, 2026-10-31): any independent re-run of SoL-Pi on Terminal-Bench, EdgeBench or another benchmark; none existed on 2026-09-19. |

Rejected:

- W1-1: integrity-incident ledger object, stored quarantine on non-subject items,
  re-verification dispatch trigger, three-arm propagation experiment. Conflict with the seven
  stored objects, TS-5, §11 deferrals and the frozen multi-agent meta-layer.
- W1-2: standing query-capture pipeline and carrier-corpus replay as a permanent build. No
  consumer: `gate-check.sh` never fires in practice, because none of the ten owner repos
  (vuoro, sprintctl, actionq, auditctl, agentops, outctl, kctl, scribectl, appservice,
  local-inference) has a `.claude/gates.json`; the refine tick re-confirmed this for agentops.
  Also rejected: building `secret-read-guard.sh` inside `#2434`; if wanted it is its own item.
- W1-3 second slice as designed in the Revision 1 synthesis: hybrid-route evidence gates and route
  qualification, retired by TS-2. Retargeted to `#2433`, see Workstream C.
- W1-4 as a `fold_into_existing` slice for 2026-09-20: the fold framing was wrong (new machinery,
  unmeasurable from repos), see the changelog.
- W1-6: per-route AgentProfile/RecipeRevision assurance object. Both objects are retired under
  TS-3 (observed, not compiled), and the routes it would bind to are retired under TS-2.
- W2-2: queued-action invalidation and dispatch gating on evidence revocation (an execution
  control plane; ActionQ, the only queue it would touch, is retiring); a source-trust registry
  (no untrusted-import consumer at one operator).
- W2-3 arm (a): full-output archive with recall handle. This is outctl's projection thesis,
  built as W0-W8 and refuted on 220 transcripts. Claude Code already spools results over about
  30 KB with a preview and path (18 recalls observed). The register's outctl row wakes only for
  a new non-outctl carrier, and this is not one.
- W2-5: any collector or assessor built outside appservice's own unit F.
- W2-6: the whole lane, see §2. Release status no longer counts against it (code and an 8B model
  are released); the substantive reasons stand.
- W1-5: building capture, sanitisation and a gate ahead of any retrieval-side change. kctl is
  retiring and self-describes as "not a search engine".

## 5. Evidence caveats per paper

Confirmed means found verbatim in the archived full text (Revision 1) or independently
corroborated against a cloned author repo or project-page branch (deep read); corroborated means
consistent across two or more secondary sources but not read from the paper; contradicted means
the digest's wording is not what the paper says; unverifiable means no primary or corroborating
source was reachable. Where the deep read changed a Revision 1 caveat, the change is marked.

- W1-1 (2609.04170): 9/5/24/62 % cohort split confirmed and corroborated by three secondary
  sources. n = 1 forensic run (100 agents, 71 Lean conjectures); "reliably reproduced" later is
  unquantified everywhere checked. The digest's 14 % is 9 + 5, not a paper figure. Ostrom-style
  commons framing; motivating evidence for the trigger, not a design.
- W1-2 (2609.09404): 720 runs = 24 attacks × 5 models × 6 frameworks at temperature 0; two judges
  give the two headline numbers (deterministic 8/720 = 1.1 %, CI 0.6-2.2; LLM panel 92/720 =
  12.8 %, CI 10.5-15.4), correctly attributed. Audio 35/72 = 49 %, CI 37-60, two models at 75 %
  and 22 %. CrewAI's 4/120 is a CrewAI-specific re-execution confound, flagged in-paper.
  **New:** no source confirms a benign/no-injection control arm; do not cite the completion rate
  as relative to a clean baseline. Block point is overwhelmingly the planning step and model
  choice dominates framework choice, which supports attaching the Decision row at the guard-hook
  deny/ask point.
- W1-3 (2609.01056): title contradicted; culturally grounded multilingual benchmark with state
  preservation as one of three gaps. 1,600 tasks, 400 personas, 9 models, single run per model per
  task. Top three within 1.2 points (49.2/48.8/48.0) confirmed. **New:** CTS is conjunctive
  (every evaluator and the preservation constraint); failure taxonomy quantified (distractor
  capture plus shell over-reach = 42.4 % of failures); 25 % of tasks double-annotated by humans,
  judge κ 0.76-0.81, 0.84 by majority vote. No model ablation; the 10/30/50-step budget check is
  protocol robustness, not an ablation.
- W1-4 (2609.03493): an RL-training paper for tool-using vision-language models (NTEP-8B from
  Qwen3-VL-8B), seven image-grounded benchmarks. **Corrected:** the Terminal-Bench comparisons
  are struck, not "unverifiable"; no evidence exists that the paper contains one (Terminal-Bench is
  arXiv 2601.11868, a different paper), so the 10 rejected quotes were quotes for a comparison that
  does not appear in this source. The regulariser number stands: calls per sample 1.55 with the
  non-repeated-goal regulariser versus 5.36 goal-only and 3.11 answer-reward-only. Human audit of
  100 blinded cases, 98 %/96 % agreement, not used for selection.
- W1-5 (2609.08887): Perplexity-authored and vendor-run, confirmed; corpus, queries and judgments
  private, no third-party reproduction possible. **Corrected:** all three judgment sets share a
  production-retrieval-pool origin (the LLM-judged set labels the same pooled rankings), not two of
  three. RRF pooling is decades-old TREC method; novelty is scale and agentic queries.
- W1-6 (2609.04894): "critical review" framing confirmed against the abstract; two authors,
  arXiv-only. 13 of 35 accepted claims are limitations, recounted from `claims.json`. Central
  thesis (action-interface expansion documented more convincingly than verified completion,
  recovery or authorization) is the conceptual source for `accepted_without_evidence`.
- W2-1 (2609.18094): 13 workers, 1,703 contributions, 165 reproductions, 145-commit/15-account
  lineage confirmed against the author repo. No controlled comparison, in the paper's own words,
  which names what one would hold constant. **New:** the first 18 of 1,703 contributions account
  for about 98 % of loss reduction; the human intervention after a five-day plateau was a
  transparency view of neglected branches, not a directive. No follow-up comparison as of
  2026-09-19 (paper three days old; recheck 2026-10-31).
- W2-2 (2609.17320): 8 worlds, 10 agents each, one mixed world (n = 1 confirmed against the
  dataset folders); 850k calls study-wide, not per world; "up to 46 h" a maximum. Grok world
  collapsed after 4 days, so 6 of 7 homogeneous worlds carry full data. **Reframed:** the
  platform's memory has no expiry, validity window or revocation at all, so the 46 h finding is the
  cost of absence, a motivating failure case for §5.1, not prior art for validity windows.
- W2-3 (2609.20519): **Narrowed:** the Terminal-Bench 4 solve counts (15/63 vs 18/63) are
  website-only, copied into a plotting script from an earlier site build; the cost figures
  ($211.12 vs $286.45, 26.3 % less) are in the arXiv text. "~94 % of Pi" is the site's rounding of
  two paper numbers (93.7 % and 94.3 %). No ablation, seeds or variance for any headline number.
  "Fused deterministic actions" contradicted two ways: the paper says "Action Fusion" and the site
  uses "deterministic" only for other mechanisms. No independent reproduction as of 2026-09-19.
- W2-4 (2609.19101): "evaluation gaming" contradicted, the paper says "reward hacking".
  **Corrected:** three models (Kimi K3, GLM 5.2, Qwen 3.8 Max) on SWE-bench Verified, DeepSWE and
  ImpossibleBench; the 57.2 %/73 % pair is GLM 5.2-specific, not the extreme. Probes versus monitor
  mixed: Kimi K3 probes catch 3.0 and 33.1 pp more on two benchmarks and miss 6.6 pp more on one;
  "probes lose by 7.9 pp" is unverifiable against primary text. Labels are LLM-judge with
  quote-grounding and triplicate consensus, not human-validated. About half of probe false
  positives are deliberations of taking a shortcut. Only 8 claims were extracted from a 133k-char
  text; the deep read re-extracted through secondary sources, see §6.
- W2-5 (2609.17885): **Flag:** at least three 2026 papers are named "ERPBench" (this one,
  arXiv 2609.04667, and erpbench.ai); cite by arXiv id. Six agents; the one closed model (Claude
  Sonnet 4.6) scored 94/100/100 % across tiers, matching an author-produced human reference; open
  models fall to 0-3 % on T2/T3. **Softened:** "up to 85 % save, as few as 3 % correct" is the
  paper's extremal wording; the exact model-plus-tier cell was not pinned. UI-TARS is a confirmed
  instance of the shape (95 % reach, 68 % save, 9 % correct).
- W2-6 (2609.15134): Ant Group authorship confirmed. +16.5 pp is the top of a 4.0-16.5 pp range
  across four harnesses on the authors' balanced 200-per-framework set; an external result exists
  (worst-case F1 88.3 % vs 80.7 % on R-Judge, ASSE-Safety, ATBench) and is more modest.
  **Updated:** code and an 8B model are released (confirmed 2026-09-19 via the author repo and a
  linked model card); Revision 1's "promised, not confirmed released" is stale.

Cross-cutting: every lane's evidence class is observational case study, benchmark, RL training
report or review; none is a controlled trial, and W1-3, W1-5, W2-1 and W2-3 state they ran no
baseline-versus-treatment comparison. That is why every kept lane lands as a cheap, reversible,
derived-projection change and not a platform build.

Prior art the deep read attached to mechanisms, for whoever implements the gated items: JTMS/ATMS,
AGM, Macaroons, RFC 7009 and object-capability revocation for EffectGrant and `revoked` (W2-2);
in-toto, SLSA v1 provenance and W3C PROV-DM for EvidenceSet lineage (W2-1); OTel GenAI
conventions (unstable, do not pin) and MCP 2026-07-28 Task ids for correlation (W1-2, W2-6);
METR's counterfactual-inflation metric shape and SWE-Bench Pro Verified's verifier hardening for
W2-4; tau-bench's `pass^k` and score re-versioning and AppWorld's row-level diffing for W1-3/W2-5.
Watchlist preprints (2609.12748, 2609.17817, 2609.19124, 2609.08481) are surfaced, not read;
2609.08481's evidence-claim taxonomy is the one to read first when EvidenceSet item metadata is
next designed.

## 6. Research method note

Revision 1: seventy-one agent runs, per lane a source checker, a code grounder, a designer and two
sceptics, then a synthesis and a completeness critic. Revision 2: one deep-read run (paper evidence
via GitHub clones and secondary corroboration, arXiv blocked) and one slices run (two read-only
grounding agents against sprintctl `7e86e23` and agentops `39f1bd3`), reconciled here. The
completeness critic's finding that `#2193`/`#2194` do not exist was wrong at the time and was not
applied; both were then retired on 2026-09-19 by the backlog reconciliation, which is a different
fact.

### Claim extraction: local inference, mechanical verification

Claims were extracted by local inference (worker-fast, Qwen3.6-35B on the 3090) over the archived
full HTML of each paper, about 1.07 million characters in total, and every quote was verified
mechanically: a verbatim match against the source text, recorded with its offset. Claims carry
`kind` (result, mechanism, method, limitation), the quote and the offset. Accepted / rejected as
not found verbatim, from `papers/summary.json`:

| Lane | arXiv | chars | accepted | rejected | accepted share |
|---|---|---|---|---|---|
| W1-1 | 2609.04170 | 62,935 | 23 | 1 | 95.8 % |
| W1-2 | 2609.09404 | 62,924 | 35 | 1 | 97.2 % |
| W1-3 | 2609.01056 | 74,620 | 36 | 0 | 100 % |
| W1-4 | 2609.03493 | 74,553 | 26 | 10 | 72.2 % |
| W1-5 | 2609.08887 | 73,036 | 18 | 6 | 75.0 % |
| W1-6 | 2609.04894 | 97,270 | 35 | 1 | 97.2 % |
| W2-1 | 2609.18094 | 55,387 | 22 | 2 | 91.7 % |
| W2-2 | 2609.17320 | 284,456 | 112 | 8 | 93.3 % |
| W2-3 | 2609.20519 | 52,639 | 22 | 2 | 91.7 % |
| W2-4 | 2609.19101 | 133,412 | 8 | 4 | 66.7 % |
| W2-5 | 2609.17885 | 37,798 | 23 | 1 | 95.8 % |
| W2-6 | 2609.15134 | 57,459 | 33 | 3 | 91.7 % |
| total | | 1,066,489 | 393 | 39 | 91.0 % |

### What this shows about W2-3(b), verified-quote reduction by a cheaper worker

This run is one data point for arm (b), the challenger WL-S1 will trial: a cheap local worker
reduces a large text to `{claim, quote, ref}` triples and a mechanical check decides what the
coordinator may trust.

- **Precision of what passes the check is high.** 393 of 432 quotes (91.0 %) matched verbatim,
  and the deep read, working from independent sources, found no case where an accepted quote was
  wrong. Trust in the accepted set held.
- **The rejections were informative, not noise.** The three worst lanes (W1-4 10/36, W1-5 6/24,
  W2-4 4/12) are where the deep read found real source problems: W1-4's rejected quotes were for a
  Terminal-Bench comparison that does not appear in the paper, a digest conflation the checker
  caught before a human read the text. Showing failures as UNVERIFIED rather than dropping them is
  what made that visible; arm (b)'s design rule is confirmed by this run.
- **Recall is unmeasured and visibly poor on at least one paper.** W2-4 yielded 8 claims from
  133k characters, all `result`, none `limitation` or `method`; the deep read had to re-extract
  through secondary sources. A verbatim check cannot see what the worker never extracted, so arm
  (b) needs a recall measure (for example, a coordinator spot-read of one section per paper) that
  this run did not have.
- **Cost was zero API spend**, which is the point of the arm; coordinator token savings and
  downstream task quality were not measured and are what WL-S1's trial must measure before arm (b)
  is preferred for any task class.

## 7. Next steps

- 2026-09-20 06:00 UTC, implementation run (devbox): WL-A1 (`#2432`), WL-D1 (`#2434`), WL-S1
  (`#2435`) in that order, per the refine tick's queue. Each lands as a Decision on its item with
  the slice as change memory; no new items, no new ledger nouns. WL-C1 (`#2436`) runs on the
  workstation when one is available.
- 2026-09-20 11:00 UTC, independent review of the landed slices against this revision and the goal
  state, findings first; findings fold into `#2432`/`#2434`/`#2435` acceptance.
- Next refine tick: file WL-A2a (the W1-4 producer count) and WL-D2 (after `#2434` lands); record
  the W1-4 re-tiering as a Decision.
- 2026-10-31, WL-R1: recheck the two watchlist items (Agora matched comparison, independent SoL-Pi
  re-run) and record the result under `docs/evidence/2026-09-19-weekly-lanes/`.
- Revision 3 only if a trigger in §4 fires or a tripwire in the target state does; otherwise this
  document is superseded by the Decisions it produces.
