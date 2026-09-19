# Agent Systems Weekly lanes: triage of the 11 and 18 September digests

**Status:** Revision 1 (2026-09-19): triage. Revision 2 follows from the scheduled deep-read and slices runs.
**Date:** 2026-09-19
**Decides against:** `2026-08-22-long-term-direction.md` (§3, §4, §7.2, §8.2, §11, §13 remain operative), `docs/direction/disposition-register.yaml` v3, `/projects/dev/agentops/docs/plans/2026-09-17-target-state.md` (TS-2, TS-3, TS-5, TS-12, TS-13)
**Evidence:** `docs/evidence/2026-09-19-weekly-lanes/papers/` (claims per paper; full texts not committed, the repo is public)

## Source and method

Two "Agent Systems Weekly" digests, 11 September 2026 (lanes W1-1..W1-6) and 18 September 2026
(W2-1..W2-6), twelve arXiv papers in total. Every digest item was treated as a candidate lane.

Method, per lane: (1) source check of the digest's claims against the archived full text
(`full_html`, 2609.* arXiv ids in the table below); (2) grounding in code, every fold target
named by file and line; (3) a design with an §8.2 experiment; (4) two independent sceptic passes,
revisions applied; then one synthesis and one completeness critic over all twelve.

Two ground rules from the goal state decided most dispositions. TS-2 retires hybrid dispatch
and OpenCode worker routing (`templates/dispatch` was deleted in S2 item 6), so nothing may fold
into `hybrid_dispatch.py` or a route qualification. §11 "Explicitly deferred" rules out a
Vuoro-owned queue, worker supervisor, model router or prompt platform, so nothing may add an
execution control plane. Everything kept lands as a derived projection or a reporting-layer
change over gates that already exist.

## 1. Disposition table

| Lane | Disposition | Owner repo | First slice or fold target | Effort |
|---|---|---|---|---|
| W1-1 Emergent cheating and whistleblowing in research swarms (2609.04170) | defer_with_trigger | sprintctl | On trigger only: supersede Decision bound append-only to the earlier terminal Decision (`sprintctl/sprintctl/decisions.py:135-150`); S4 derived `contested_accept`; downstream query only if depcore dependants exist outside the closure batch | S |
| W1-2 MMPIBench multimodal prompt injection (2609.09404) | run_now (narrowed) | agentops | Decision rows at every PreToolUse deny/ask point (`bounded-read-guard.sh`, `nfs-workspace-guard.sh`, `forge-sandbox-guard.sh`, `gate-check.sh`, `secret-read-guard.sh`) appended to `gates-$SESSION.jsonl`; carrier replay deferred | S |
| W1-3 WorldBench (2609.01056; real title "WorldBench: Culturally Grounded Benchmark for Multilingual Agents") | fold_into_existing | local-inference; sprintctl (second slice) | `outcome ∈ {completed, completed_with_collateral_change, incomplete}` derived from existing `accepted` and `violated_constraint` in `exp-tier-effect.py`, `analyse-tier-effect.py`, `results-to-scorecard.py`; second slice retargeted, see Workstream C | S |
| W1-4 Necessary Tool-Evidence Paths (2609.03493) | fold_into_existing | sprintctl | `acceptance_contract.evidence_obligations` key (`releases.py: normalize_acceptance_contract`) plus a derived `unmet_obligations` report query, not a `decision_error` block; step-0 producer-count precondition | S-M |
| W1-5 Q2D-Web retrieval on agent-written queries (Perplexity, 2609.08887) | defer_with_trigger | agentops | No build. On trigger, one §8.2 probe under `_projects/retrieval-q2d-probe/` using acceptance-lab scorers | S (deferred) |
| W1-6 From language models to world-acting systems (2609.04894) | fold_into_existing (narrowed) | sprintctl | `accepted_without_evidence` finding kind in `work.read.unbound` (`sprintctl/sprintctl/unbound.py`); route-level AgentProfile rejected | S |
| W2-1 Agora: Git as shared memory for collective autoresearch (2609.18094) | fold_into_existing (minimal) | sprintctl | One scope-note line on the change-memory reconstruction item (#2057 today): log claim-level supersession/derivation/corroboration gaps as observations; no code | S (near zero) |
| W2-2 Emergence World adversarial stress-testing (2609.17320) | reference, no build | sprintctl (if ever) | No fold now. Supporting evidence for §5.1 EvidenceSet validity and expiry, which are derived, not stored. A `revoked` reason and a completion-time gate are rejected (no producer, wrong code path) | none |
| W2-3 SoL-Pi efficient harness design (2609.20519) | fold_into_existing | agentops | Reuse the context-economy study instrument (`outctl/studies/context-economy/{measure,classes,waste}.py`) as evidence under `agentops/scripts/context_economy/`; add `verify_quotes.py` for lever C (Explore delegation); arm (a), full-output archive with recall handle, rejected | S-M |
| W2-4 Monitoring reward hacking through internal representations (2609.19101) | fold_into_existing (narrowed) | agentops | `check_trajectory_flags.py` (test/gate-file weakening, rework-round churn) run locally at the verify stage; advisory flag triggers one extra Sonnet review-synthesis pass on the flagged unit only; scope-drift signal folds into the scope-declaration trial (#2058 today) | S |
| W2-5 ERPBench state-grounded acceptance (2609.17885) | fold_into_existing | appservice | Delivery unit F (off-cluster collector, expiring receipt, assessor) from `appservice` `2026-08-27-repository-assurance-and-operational-evidence.md`, line 16 still reads "not started"; agentops `_projects` ExperimentRecord compares accept basis with F's verdict | M |
| W2-6 HazardAuditor execution-grounded safety across harnesses (2609.15134) | reject | vuoro-evidence (if ever) | No build. `EFFECT_UNCERTAIN`, `GrantUse.UNCERTAIN_USE` and OBSERVATION reconciliation already exist; the gap is the dark evaluator (0 callers), an existing S4/S5 wiring item | none |

Item ids (#2057, #2058) are named for orientation only. The VUORO-CP and dispatch-lifecycle
items in the served backlog are being reconciled against the 2026-09-17 target state and may
be retired; no trigger below depends on an item id.
Update, same day: `agentops/docs/assessments/2026-09-19-backlog-reconciliation.md` (agentops #182)
retires #2057 and #2058 along with the deleted hybrid task-packet substrate. So W2-1's scope-note line
and W1-3's second slice need a new host item; revision 2 names or files it.

## 2. Merged workstreams and sequencing

### Workstream A: evidence and Decision lifecycle hardening (sprintctl, S3/S4)

Folds W1-1, W1-4, W1-6 and the W2-2 reference. All four touch `Release.acceptance_contract`,
`Decision.evidence_digests` and the S4 "one evidence home". None adds a ledger noun. All are
read-only derived projections or narrow Decision-kind extensions, consistent with TS-5 (Decision
is the sole terminal writer, no parallel acceptance records) and with the register's rule that
EvidenceSet validity and expiry are derived.

1. Now, no dependency (S): W1-6 `accepted_without_evidence` as a fourth kind in
   `work.read.unbound`. Reports, never blocks, accepts on `review_required` releases with no
   evidence digests, split by whether the accept came through the `status done`/outbox alias or
   an explicit `item decide`. Tests: `tests/test_releases.py`, `tests/test_decisions.py`,
   `tests/pg/test_releases.py`, `tests/test_served_decisions.py`.
2. Next (S-M): W1-4 `evidence_obligations` on `acceptance_contract` plus a pure
   `unmet_obligations()` report query. Step 0 is a producer count: at least five sprint-559 items
   with ingested evidence refs; below that, record a defer Decision and stop (TS-12, measure
   before declaring).
3. At S4 (evidence-home migration): W2-2's read-time expiry check, `decisions_on_expired_evidence`,
   porting the vuoro-evidence reducer's `expiry_of()` and joining `work_decision.evidence_digests`
   to validity computed at read time. No stored expiry event, no new reason value, no gate.
4. Trigger-gated only: W1-1's supersede-binds-to-terminal-Decision rule and `contested_accept`
   activate on the trigger in §4. No code before that; when it activates it reuses step 1's
   alias/actor classification.

Dependencies: 1 and 2 share a read surface but build independently; 3 waits for S4; 4 waits for
its trigger, not for 1-3.

### Workstream B: change memory for reconstruction (sprintctl)

Folds W2-1. One line on the reconstruction item's scope note: log any claim-level supersession,
derivation or corroboration link the reconstructing session needed but could not get from Git,
handoff/v1 and item Decisions, as an observation with `evidence_ref`. Zero code, rides an
already scheduled item. Its output decides whether Workstream A ever needs a `claim` evidence
kind beyond what TS-13 already plans.

### Workstream C: outcome class and state-grounded acceptance (local-inference, sprintctl, appservice)

Folds W1-3 and W2-5. One mechanism in two places: grade against durable state, not self-report,
and split "did the action" from "did the right thing".

1. Now, offline, zero model spend (S): W1-3's local-inference rescore, the first experiment in
   §3. Retargeted to `probe-escalation.sh`'s escalation JSONL, the one source with observed
   accepted-and-tampered runs, not the tier corpus (0 tampered rows, no signal).
2. Second slice, retargeted (S): the synthesis folded this into `hybrid_dispatch.py` evidence
   gates and the hybrid-route qualification corpus. Both are retired under TS-2
   (`hybrid_dispatch.py` survives only in the `base-main` snapshot and stale worktrees). The
   slice now lands in sprintctl's scope-declaration trial (#2058 today): when a Release carries a
   declared write scope, `collateral` = any diff path outside it, and the Decision's evidence
   digest carries the same three-way `outcome`. Condition to start: the scope-declaration trial
   has produced at least one Release with a declared scope and a diff digest. Vuoro projects the
   field onto EvidenceSet/ExperimentRecord exports and never computes or adjudicates it.
3. Parallel, appservice-owned (M): W2-5 Delivery unit F. Already specified in appservice's own
   plan, not started. Vuoro/agentops involvement is limited to an `_projects` ExperimentRecord
   over F's output once F has completed two collection cycles.

### Workstream D: harness denial and trajectory-signal recording (agentops hooks)

Folds W1-2 and W2-4. Both add a local, advisory signal to the per-session gate log that
`log-session-cost.sh` already drains into auditctl. No new store, no new runner.

1. First (S): W1-2's decision-row helper, one shared function called from every guard's deny/ask
   emit point, appending `{kind:"decision", ts, hook, rule_id, tool, policy_decision}` to
   `gates-$SESSION.jsonl`. `log-session-cost.sh` partitions `decisions` out of the `gates` array so
   `rework_rounds` keeps its shape. Test in `agentops/hooks/tests/`.
2. Second, reuses 1 (S): W2-4's `check_trajectory_flags.py` reads the session gate log plus
   `git diff base..head` and flags (a) test/gate-file weakening and (c) `rework_rounds >= 3` or
   gate-command churn. Runs locally at the verify stage, not in CI (no gate log on a runner). A
   flag triggers one extra Sonnet `review-synthesis` pass on the flagged unit; no Opus escalation,
   never rejects, never touches sprintctl state.
3. The scope-drift signal folds into the scope-declaration trial (Workstream C step 2) rather
   than a separate build.

### Standalone: context-economy quote verification (agentops)

W2-3, kept separate because the mechanism is delegated-reading trust, not denial or trajectory
logging. outctl is retired and stays retired. What moves is evidence and its instrument: the
context-economy study code (`measure.py`, `classes.py`, `waste.py`) and its plan rows are
relocated under `agentops/scripts/context_economy/` because they measure the current stack
(§7.2 control), not because outctl's projection thesis is reopened. Phase 1/2 gates (Bash share
< 28 %, file-read share < 45 %) were never measured; measure them first, then trial arm (b): the
delegated worker returns `{claim, quote, ref}` and `agentops/scripts/verify_quotes.py` checks
each quote verbatim (whitespace-normalised) against `path:Lstart-Lend` or a spool file plus
sha256; failures are shown as UNVERIFIED, never dropped.

Today's extraction run is a first real data point for arm (b): a cheap local worker
(worker-fast, Qwen3.6-35B on the 3090) reduced twelve full texts to claims with verbatim quotes,
and the mechanical check accepted 393 of 432 quotes (~91 %), rejecting 39 as not found verbatim.
That is the shape of the challenger arm at zero API cost; it does not yet measure coordinator
token savings or task quality, which the trial must.

### Rejected outright

W2-6. The transferable idea (judge by execution outcome, not transcript) is already implemented
in vuoro-evidence. Building a canonical multi-harness event envelope would violate §3.2
(normalize edges, not interiors) and the dossier's permanent exclusion of tool-call telemetry.

## 3. First experiment

Pick: W1-3's local-inference outcome-field rescore on the escalation-probe data. It needs no new
agent run, has a known positive signal (both rows of
`local-inference/benchmarks/results/2026-08-19T120204-escalation.jsonl` are `accepted=true` and
`violated_constraint=true`, verified today), touches no lifecycle gate, and is the one lane where
both sceptic passes converged on the same correction. Every other candidate starts from zero
known occurrences.

```yaml
id: exp-2026-09-local-inference-outcome-collateral
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
  tamper next to pass, also unfolded.
challenger: >
  Pure function over existing booleans:
  outcome = 'incomplete' if not accepted
            else 'completed_with_collateral_change' if violated_constraint
            else 'completed'.
  Applied identically to exp-tier-effect.py output, probe-escalation.sh output and
  results-to-scorecard.py. Rows lacking violated_constraint get outcome = null and
  unavailable_reason = "no_violated_constraint_field". No new gates, no model spend.
task_sample: >
  All parseable rows in local-inference/benchmarks/results/*.jsonl, counted today:
  219 rows across 25 files (88 malformed lines skipped and reported). 38 rows carry
  accepted=true: 2026-08-19-tier-effect.jsonl (18, 0 tampered),
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
  - malformed JSONL lines per file (88 today), reported so the corpus can be repaired
falsifier: >
  Retire the outcome field if the rescore surfaces zero
  completed_with_collateral_change rows (the derivation is wrong), or if per-arm
  rankings are unchanged and no row was previously invisible under the TAMPER
  free-text convention (the field adds nothing a reader of scorecard.csv does not
  already have).
rollback: >
  The field is additive: accepted and the TAMPER note are kept unchanged, the
  rescore writes a new scorecard beside the old one, and the schema change to
  probe-escalation.sh is one added key. Reverting is deleting the outcome column
  and the derivation function; no stored ledger row is created (the result lands
  as a Decision with this record as change memory, per the dossier's noun-drop rule).
first_slice: >
  In /projects/dev/local-inference: (1) add outcome beside accepted in
  exp-tier-effect.py; (2) count by outcome in analyse-tier-effect.py's per-arm
  summary; (3) add an outcome column to results-to-scorecard.py, derived for
  existing records, TAMPER note kept; (4) add task_id/arm to probe-escalation.sh's
  JSONL and backfill the two rows; (5) rescore all results and record class counts
  as a Decision with rationale.
effort: S
depends_on: []
```

## 4. Deferred lanes with triggers; rejected lanes with reasons

Triggers are observable conditions. None depends on a backlog item id surviving the current
reconciliation.

| Lane / part | Trigger |
|---|---|
| W1-1 full contest machinery (supersede rule, `contested_accept`, downstream query) | The S3 truth-audit quarter records a post-S3 accept Decision that independent evidence later contradicts, for example an auditctl observation with `confirms=false` referencing the Decision id. Zero cases today; sprint 545's 26 legacy re-marks do not count, no accept Decision exists for them to contest. |
| W1-2 carrier replay | Either (a) about two weeks of decision rows show denials whose origin the rows alone cannot explain (injection-like or false-positive pattern), or (b) a baseline policy Rego with tests exists and an observation-mode run over a full backlog has produced a denial set to replay against. |
| W1-4 per-call obligation reference, expected-gain stopping, OTel redundancy measurement | Both: (i) the goal state changes to permit a Vuoro-owned worker supervisor (§11 explicitly defers it), and (ii) emit-only harness traces with evidence-goal tagging exist in agentops (the role-scoped fitness and routing evidence work, agentops-owned, not sprintctl) and show redundant-call rates the post-hoc query cannot correct. |
| W1-5 Q2D-Web method (queries by phase, citation-derived labels, pooled top-k judging) | Any of: (1) an operator-owned retrieval surface is proposed (local retriever/index, or a search provider replacing WebSearch); (2) a context-compiler or handoff change alters retrieved evidence; (3) the direction's metric "evidence retrievals required after initial handoff" rises, or a handover attributes a wrong outcome to missing or stale retrieved evidence. |
| W2-1 full Agora comparison (diversity-aware selection, notes versus graph) | No numeric trigger exists; nothing detects duplicated open-ended investigations today. Reopen by a new lane proposal once sprintctl holds parallel open-ended investigations on one question. |
| W2-2 `revoked:<collector\|source>` reason and untrusted-ingest provenance | Either (i) a real producer is observed: a collector version found faulty, or a kctl lesson/claim import retracted after S4 (TS-12, TS-13); or (ii) one of our own runs shows untrusted content persisting into agent memory or state and being acted on later. |

Rejected:

- W1-1: integrity-incident ledger object, stored quarantine on non-subject items,
  re-verification dispatch trigger, three-arm propagation experiment. Conflict with the seven
  stored objects, TS-5, §11 deferrals and the frozen multi-agent meta-layer.
- W1-2: standing query-capture pipeline and carrier-corpus replay as a permanent build. No
  consumer: `gate-check.sh` never fires in practice, because none of the ten owner repos
  (vuoro, sprintctl, actionq, auditctl, agentops, outctl, kctl, scribectl, appservice,
  local-inference) has a `.claude/gates.json`.
- W1-3 second slice as designed: hybrid-route evidence gates and route qualification, retired by
  TS-2. Retargeted, see Workstream C.
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
- W2-6: the whole lane, see §2.
- W1-5: building capture, sanitisation and a gate ahead of any retrieval-side change. kctl is
  retiring and self-describes as "not a search engine".

## 5. Evidence caveats per paper

Confirmed means found verbatim in the archived full text; contradicted means the digest's wording
is not what the paper says; unverifiable means the number came through a summariser or a page
outside the paper.

- W1-1 (2609.04170): 9/5/24/62 % cohort split and shared-channel contagion confirmed. Rests on
  one forensic run (100 agents, 71 conjectures); "reliably reproduced" later is unquantified.
  The digest's 14 % is 9 + 5, not a paper figure.
- W1-2 (2609.09404): "1 % completed vs 12.8 % attempted" confirmed, but it is about 8 completions
  in 720 single-trial runs at temperature 0. The 49 % audio figure covers 72 cells and two
  models, CI 37-60 %.
- W1-3 (2609.01056): title contradicted; the paper is a culturally grounded multilingual
  benchmark with state preservation as one of three gaps. CTS formula and 49.2 % top score
  confirmed; the top three models sit within 1.2 points (48.0-49.2).
- W1-4 (2609.03493): the Terminal-Bench comparisons are unverifiable (10 of 36 quotes rejected,
  the highest rate in the set). The redundancy result, calls per sample 1.55 to 5.36 without the
  regulariser, is directly quoted and is the only number used.
- W1-5 (2609.08887): Perplexity-authored and vendor-run, confirmed. Two of three judgment sets
  originate in Perplexity's own systems, so "stable across judgment methods" partly reflects
  shared origin. The benchmark data is private; any implication of third-party reproduction is
  contradicted. 6 of 24 quotes rejected.
- W1-6 (2609.04894): "critical review" framing confirmed against the abstract. Two-author,
  non-peer-reviewed narrative synthesis; 13 of 35 accepted claims are limitations.
- W2-1 (2609.18094): 13 workers, 1,703 contributions, 165 reproductions, 145-commit/15-account
  lineage confirmed. The paper states no controlled comparison was run, and a human intervened
  to break the community out of its first local optimum.
- W2-2 (2609.17320): per-world numbers confirmed (8 worlds; 850k calls total, not per world).
  "Up to 46 h" persistence latency is a maximum. The mixed-versus-homogeneous population effect
  is n = 1. Largest text (284k chars) and largest claim set (112).
- W2-3 (2609.20519): "~94 % of Pi" and Terminal-Bench 15/63 versus 18/63 are not in the arXiv
  text; they come from the project's GitHub Pages site, no ablation, seeds or variance.
  "Fused deterministic actions" contradicted: the source says "Action Fusion" and never
  "deterministic".
- W2-4 (2609.19101): "evaluation gaming" contradicted, the paper says "reward hacking". 57.2 %
  and 73 % hack rates are LLM-judge labelled on one open-weight model; probe-versus-monitor
  results are mixed, not matched (probes lose by 7.9 pp on one model). Only 8 claims extracted,
  all results, 4 rejected; the deep-read run must re-extract limitations.
- W2-5 (2609.17885): "85 % save rate, 3 % correct" is a worst-case single cell (a 7B open model
  on a 20-run tier). The one closed model tested (Claude Sonnet 4.6) scored 94-100 % DB-correct
  on every tier, so the headline gap barely exists for the model family in use here.
- W2-6 (2609.15134): Ant Group authorship and +16.5 pp confirmed, a best case on the authors'
  own 200-trajectory subset. Code and models are promised, not confirmed released.

Cross-cutting: every lane's evidence class is observational case study, benchmark, or review;
none is a controlled trial, and W1-3, W1-5, W2-1 and W2-3 state they ran no
baseline-versus-treatment comparison. That is why every kept lane lands as a cheap, reversible,
derived-projection change and not a platform build.

## 6. Research method note

Seventy-one agent runs: per lane a source checker, a code grounder, a designer and two sceptics,
then a synthesis and a completeness critic. The critic's finding that #2193/#2194 do not exist
was wrong (both are pending sprintctl items in sprint 426) and was not applied; its
owner-repo correction on the role-scoped fitness work (agentops, not sprintctl) and its
`.claude/gates.json` count were applied.

Claim extraction ran on local inference (worker-fast, Qwen3.6-35B on the 3090) over the
archived full HTML of each paper, with every quote checked verbatim against the text.
Accepted / rejected as not found verbatim, from `papers/summary.json`:

| Lane | arXiv | chars | accepted | rejected |
|---|---|---|---|---|
| W1-1 | 2609.04170 | 62,935 | 23 | 1 |
| W1-2 | 2609.09404 | 62,924 | 35 | 1 |
| W1-3 | 2609.01056 | 74,620 | 36 | 0 |
| W1-4 | 2609.03493 | 74,553 | 26 | 10 |
| W1-5 | 2609.08887 | 73,036 | 18 | 6 |
| W1-6 | 2609.04894 | 97,270 | 35 | 1 |
| W2-1 | 2609.18094 | 55,387 | 22 | 2 |
| W2-2 | 2609.17320 | 284,456 | 112 | 8 |
| W2-3 | 2609.20519 | 52,639 | 22 | 2 |
| W2-4 | 2609.19101 | 133,412 | 8 | 4 |
| W2-5 | 2609.17885 | 37,798 | 23 | 1 |
| W2-6 | 2609.15134 | 57,459 | 33 | 3 |
| total | | | 393 | 39 |

Claims carry `kind` (result, mechanism, method, limitation), the quote and its offset. Absence
claims (no ablation, no variance, no ground truth) are provisional until the deep-read run
checks the bodies, not only the abstracts and result tables.

## 7. Next steps

Scheduled cloud runs:

- 2026-09-19 10:30 UTC: deep-read of the twelve full texts (limitations, ablations, variance;
  re-extract W2-4) and slice design for Workstreams A step 1, C step 1, D step 1 and the
  standalone lane.
- 2026-09-19 12:30 UTC: synthesis, revision 2 of this document, with the deep-read caveats
  replacing §5 where they differ.
- 2026-09-20 06:00 UTC: implementation of the S-effort slices: the first experiment (§3),
  W1-6 `accepted_without_evidence`, W1-2 decision rows, W2-1 scope-note line. Each lands as a
  Decision with its slice as change memory; no new ledger nouns.
- 2026-09-20 11:00 UTC: independent review of the landed slices against this plan and the goal
  state, findings first.

Revision 2 records, per lane, whether the deep read changed a disposition.
