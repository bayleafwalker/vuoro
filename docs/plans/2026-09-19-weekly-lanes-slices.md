# Weekly lanes: first slices (2026-09-19)

**Status:** Draft specification, no code changes.
**Date:** 2026-09-19
**Follows:** `2026-09-19-agent-systems-weekly-lanes.md` (the triage; "the plan" below).
**Scope:** every `run_now` or `fold_into_existing` lane in the plan, and the plan's five
merged workstreams (A-D, Standalone). Deferred and rejected lanes are out of scope except
where a merged workstream step is itself trigger-gated (noted, not sliced).
**Method:** repo grounding via two read-only agents against sprintctl HEAD `7e86e23` and
agentops HEAD `39f1bd3` on 2026-09-19, plus direct reads of the goal-state documents. Every
file:line below was re-checked against those commits; none is carried over from the plan
without verification.
**Repos touched by this document:** none besides `vuoro` (this file). No code, no other
repo's files, no sprintctl writes.

## 0. The grounding run found something the plan didn't know yet

The plan's own §1 update note says: "W2-1's scope-note line and W1-3's second slice need
a new host item; revision 2 names or files it." That gap is already closed, by an agentops
process this run discovered rather than caused:

`agentops` `docs/assessments/2026-09-19-backlog-reconciliation.md` (agentops #182, a
95-agent read-only assessment) retired sprintctl `#2057` and `#2058` — the two item ids the
plan uses as placeholders for Workstream B and Workstream C step 2 — because both were tied
to the deleted hybrid task-packet substrate (`0baa680`). Note what `#2057` and `#2058`
actually *were*: `#2057` = "Independent scope reconstruction from artifacts" and `#2058` =
"Randomized trial: scope declaration control," both hybrid-dispatch qualification work, not
the "change-memory reconstruction item" the plan's prose describes. The plan's own citation
of these ids was already a naming collision with unrelated retired items, not a reference to
live host items — its "(named for orientation only)" hedge in §1 was correct to add.

`agentops` `docs/assessments/lane-loop/refine-2026-09-19T1056Z.md` (10:41 UTC, after this
plan's PR #94 merged at 07:20 UTC) then *acted* on that assessment as sprintctl Decisions
(TS-5: every retirement is a Decision, not a status alias) and filed five new sprint-559
intake items that map directly onto this plan's workstreams:

| sprintctl id | Maps to | Tier | Blocked-on |
|---|---|---|---|
| `#2432` | Workstream A step 1 (W1-6, `accepted_without_evidence`) | fast-build | none |
| `#2433` | Workstream C step 2 + Workstream B (W1-3 second slice + W2-1 scope note); successor of `#2058` | frontier-plan | S3 Release (ordering, not a gate) |
| `#2434` | Workstream D step 1 (W1-2, decision-row helper) | fast-build | none |
| `#2435` | Standalone (W2-3, context-economy gates + relocation) | fast-build | none (outctl `94840d7` on devbox) |
| `#2436` | Workstream C step 1 (W1-3 first slice, local-inference rescore) | local-lane | host: local-inference is workstation-only |

The same refine tick's queue (§5.1) states the intent explicitly: *"the 06:00 UTC 2026-09-20
implementation run should find these ids and record Decisions on them, not new items."*
Section 4 below therefore specifies slices against these five existing ids rather than
proposing new ones. Workstream A steps 2-4 and Workstream D step 2 are **not** filed yet
(step 2 needs a producer count first per TS-12; steps 3-4 are S4- and trigger-gated) — those
are specified below as slices without a host item, for whoever files them next.

This is itself evidence for the plan's own §3.6 point (change memory before statistics):
two independently scheduled runs converged on the same five slices from different
directions. Revision 2 of the triage document should cite `#2432`-`#2436` directly.

## 1. Grounding corrections to the triage document

Confirmed accurate unless noted. "New" means the plan is right that nothing exists yet.

### W1-6 / Workstream A step 1 — `work.read.unbound`

- `sprintctl/sprintctl/unbound.py` confirmed, 190 lines. Current `CATEGORIES = ("legacy_done",
  "decided_unreleased", "released_undecided")` — three kinds today; `accepted_without_evidence`
  would be the fourth, as the plan says.
- `review_required` is `DEFAULT_ACCEPTANCE_CONTRACT = {"review_required": True}`
  (`releases.py:32`), stored on `work_release.acceptance_contract`. **Correction:** it is
  written and stored but never read or branched on anywhere else in the codebase today — it
  is inert metadata, not an enforced gate. The plan's phrasing ("review_required releases")
  should be read as "releases carrying the default contract key," not as a gate the code
  currently honors.
- **Correction, material:** the plan's split "whether the accept came through the `status
  done`/outbox alias or an explicit `item decide`" has no stored provenance marker. Two
  distinct code paths exist — alias: `db.py:1598-1604` and `authority.py:283,314-324`, both
  calling `_decide_locked`/`decision_record` with **no rationale and no evidence_digests**
  (the CLI `item status` command has no `--rationale`/`--evidence` flags at all: `commands/work.py:1446-1502`);
  explicit: `commands/work.py:1527-1630`'s `item decide`, which *requires* `--rationale` and
  accepts repeatable `--evidence` — but `work_decision` has no column recording which path
  produced a given Decision row. The practical proxy is `rationale == ""`, which is true for
  every alias-path accept and false for every well-formed explicit accept (a `--rationale`
  is required there). The first slice must use this proxy explicitly, not assume a stored flag.

### W1-4 / Workstream A step 2 — `acceptance_contract.evidence_obligations`

- `releases.py:94-102`'s `normalize_acceptance_contract` confirmed: it canonicalizes any JSON
  object, no fixed schema beyond "must be an object." Zero existing `evidence_obligations`
  key anywhere in the repo — confirmed new.
- **Correction:** the plan's step-0 "producer count" precondition has no existing pattern to
  reuse. The only analogues are single-item minimums (`decisions.py:186-189`: "at least one
  evidence digest"; `observations.py:234,372`: "at least one evidence_ref"), never a
  cross-item count over a sprint. The "at least five sprint-559 items with ingested evidence
  refs" check is new machinery, not a fold.
- **Correction:** sprint-559 item/evidence-ref counts are **not determinable from either
  repo's git history** — sprintctl ships no committed DB, fixture, or snapshot with live
  sprint data (`docs/sprint-snapshots/*.txt` has no sprint-559 file; repo-wide grep for
  "559" is empty except an unrelated lockfile hash). This step-0 check must be run against
  live sprintctl state (`sprintctl sprint show 559` or equivalent), not against the repo.
  The lane-loop refine tick above independently reached the same conclusion (§5.3: "step 2
  needs a producer count first (TS-12)") and left it unfiled for exactly this reason.

### Workstream A step 3 — W2-2 reference, `decisions_on_expired_evidence`

- Confirmed absent repo-wide (zero grep hits for `decisions_on_expired_evidence`,
  `evidence_obligations`, an evidence-tied `revoked` reason). Matches the plan's "S4, no code
  yet" framing.
- `work_decision.evidence_digests`: SQLite `db.py:871-874` (`TEXT NOT NULL DEFAULT '[]' CHECK
  (json_valid(...) AND json_type(...) = 'array')`), Postgres `pg.py:1762-1763` (`jsonb`
  equivalent). Read today via `json.loads` on fetch (`db.py:2273-2275`) and via
  `json_array_length(...) > 0` / `jsonb_array_length(...) > 0` predicates in legacy-recovery
  triggers (`db.py:1154-1155`, `pg.py:2409-2411`) — an existence check, not an expiry check.
  No read-time expiry join exists.

### W1-1 / Workstream A step 4 — `decisions.py:135-150`

- **Correction:** lines 135-150 are `transition_error`, a general terminal-state guard
  ("Item #{id} is terminal; it accepts no further decisions"; accept requires `active`), not
  a supersede-specific "bind to terminal Decision" rule. No such rule exists anywhere in the
  codebase in any form (checked `decisions.py`, `db.py`, `pg.py` for every `supersede`
  reference); `superseded_by_item_id` only records which *item* supersedes, with a
  self-reference check (`db.py:877,882-883`). This citation is the nearest fold point, not
  existing behavior — correct the plan's framing in revision 2, but the disposition
  (trigger-gated, no code now) is unaffected since the rule is new either way.

### W2-1 / Workstream B — change-memory scope note

- No "#2057," "#2058," "change-memory," "reconstruction," or "scope-declaration" concept
  exists in sprintctl's own code or docs (all zero grep hits) — sprintctl backlog state is
  runtime data, not committed. §0 above resolves the item-id question from the agentops side.

### Workstream C step 2 — declared write scope on Release

- Confirmed genuinely new: `work_release` columns are `id, work_item_id, item_revision,
  revise_count, release_digest, acceptance_contract, context_refs, created_at, actor`
  (`db.py:1020-1041`, `pg.py:2158+`). No `scope`/`write_scope`/`scope_paths` column; zero
  grep hits for `scope|collateral` near Release/WorkRelease code. This aligns with the
  direction doc's own description of what a `WorkRelease` should carry (§4.1: "bounded
  context and source references") — a declared write scope specializes an already-intended
  field, it does not add a new noun.

### W1-2 / Workstream D step 1 — guard-hook decision rows

- Four of the five named hooks exist and emit PreToolUse `deny`/`ask` JSON to **stdout
  only**, no file write: `bounded-read-guard.sh:59-64`, `nfs-workspace-guard.sh:79-98`,
  `forge-sandbox-guard.sh:22-25`, `gate-check.sh:29-36` (which emits both `deny` and `ask`
  depending on `operator-actioned`/`operator-approved`).
- **Correction:** `secret-read-guard.sh` **does not exist**. It is named only inside a
  comment in `bounded-read-guard.sh:22` describing a hypothetical fail-closed sibling. The
  slice below touches four hooks, not five; building `secret-read-guard.sh` itself is a
  separate, unscoped piece of work this plan does not ask for.
- `gates-$SESSION.jsonl` already exists, written by a *different* hook,
  `hooks/gate-log.sh:40-59` (PostToolUse, not the deny/ask guards), one flat `{ts, cmd,
  exit, signal, ok}` row per matched gate command (`GATE_PATTERN` at `gate-log.sh:20`:
  pytest, `run_round_checks`, `hybrid_dispatch(.py)?` gate, cargo test, unittest discover).
  There is no `"gates"` key inside the file itself.
- `log-session-cost.sh` (Stop hook) reads that file (`log-session-cost.sh:63,70-74`),
  computes `rework_rounds` (`:75-81`: a failed row `ok==false` followed by a later row with
  the same `cmd`), and publishes the whole array to auditctl under metadata key `gates`, plus
  `rework_rounds` (`:118-123`).
- **Correction, material — a real implementation risk, not just a citation fix:** the
  plan's line 113-114 ("`log-session-cost.sh` partitions `decisions` out of the `gates`
  array so `rework_rounds` keeps its shape") describes a change that does not exist yet. If
  the new decision-row helper starts appending `{kind:"decision", ...}` rows into the same
  `gates-$SESSION.jsonl` file, `log-session-cost.sh:75-81`'s `rework_rounds` jq has no `kind`
  discriminator today and will silently try to retry-match decision rows against gate-command
  rows (neither has the other's `cmd`/`ok` shape). **The slice must add the `kind != "decision"`
  filter to `log-session-cost.sh` in the same change that starts writing decision rows**,
  not as a follow-up — otherwise the very first decision row written corrupts `rework_rounds`
  for every session from that point on.
- `hooks/tests/` exists (12 scripts); `test-gate-log.sh` is a precedented oracle pattern
  (REQ-numbered, drives the hook via stdin exactly as the harness does, isolates
  `AGENTOPS_GATE_LOG_DIR`) to copy for a new decision-row test.

### W2-4 / Workstream D step 2 — `check_trajectory_flags.py`

- Confirmed absent (no file, no equivalent logic anywhere). `rework_rounds` is computed
  exactly once, in `log-session-cost.sh:75-81` (see above) — a retry-of-a-failed-gate-command
  count, not a diff/trajectory analysis. The "reuses 1" framing in the plan (line 115-116) is
  accurate only for the `rework_rounds >= 3` half; the `git diff base..head` test/gate-file
  weakening half has no precedent and is new code.

### W2-3 / Standalone — context-economy relocation

- `agentops/scripts/context_economy/` does not exist. `verify_quotes.py` does not exist
  anywhere in agentops. No hits for "context-economy," "Bash share," or "file-read share" in
  any agentops doc or script. The Phase 1/2 gate thresholds (Bash share < 28%, file-read
  share < 45%) the plan wants measured first do not appear anywhere in agentops today,
  confirming they are unmeasured, not merely unenforced. `hooks/bounded-read-guard.sh:5-10`
  cites the same underlying 220-transcript study but not these terms or thresholds — it is
  not the same artifact. `#2435` (§0) already notes the precondition `outctl at 94840d7 is on
  devbox` for relocating the study code, which this environment cannot verify (`outctl` is
  not cloned here — see §6).

### TS-2 confirmation — `hybrid_dispatch.py`

- Confirmed deleted: `git log --all -- '**/hybrid_dispatch.py'` shows only the S2 item 6
  deletion commit (`0baa680`); no `templates/dispatch/` directory exists in this checkout.
  Two harmless textual survivals: `gate-log.sh:20`'s `GATE_PATTERN` still matches the string
  `hybrid_dispatch(.py)?` (matching historical gate-command rows, not requiring the file to
  exist), and its test fixture strings. The plan's claim that the file "survives... in stale
  worktrees" is unverifiable from this checkout (no other worktrees exist here) — neither
  confirmed nor contradicted, just outside what this environment can see.

## 2. First slices

Ordered as specified in §3 (dependency order). Each entry: schema/contract diff, files,
tests, CI check, falsifier.

### Slice 1 — Workstream A step 1: `accepted_without_evidence` (sprintctl, `#2432`)

- **Contract diff:** add a fourth string to `unbound.py`'s `CATEGORIES` tuple:
  `accepted_without_evidence`. No new object; extends the existing enum `work.read.unbound`
  already returns.
- **Derivation:** a finding for any item whose latest terminal Decision has `kind="accept"`,
  `evidence_digests == []`, on a Release whose `acceptance_contract` still carries the
  default `{"review_required": true}` key. Split the finding by `rationale == ""` (alias
  path) vs non-empty (explicit `item decide` with no evidence attached) — see the §1
  correction; there is no better provenance signal today.
- **Files:** `sprintctl/sprintctl/unbound.py` (new category + derivation function);
  `sprintctl/sprintctl/commands/` wherever `unbound` is surfaced to CLI/served output (report
  field only, no new flag).
- **Tests:** extend `tests/test_releases.py` and `tests/test_decisions.py` with fixtures for
  both the alias-path and explicit-decide-without-evidence cases; mirror in
  `tests/pg/test_releases.py`; add a served-path case to `tests/test_served_decisions.py`
  (alias accepts happen there too, per `authority.py:283,314-324`).
- **CI check:** existing sprintctl test suite gate (no new CI job; this is a pure-function
  addition to an already-tested report path).
- **Falsifier:** run the new derivation against live sprint state. If it returns zero
  findings across every open and recently closed sprint, the alias path is not actually
  producing evidence-free accepts in practice and the finding kind should be retired before
  it ships — check this before merging, not after.

### Slice 2 — Workstream D step 1: guard-hook decision rows (agentops, `#2434`)

- **Contract diff:** one new JSONL row shape appended to the existing `gates-$SESSION.jsonl`
  file: `{kind:"decision", ts, hook, rule_id, tool, policy_decision}` where `policy_decision
  ∈ {deny, ask}`. Reuses the existing per-session gate log; no new store.
- **Files:** a new shared shell function (e.g. `hooks/lib/emit-decision.sh`) sourced by
  `bounded-read-guard.sh`, `nfs-workspace-guard.sh`, `forge-sandbox-guard.sh`, and
  `gate-check.sh` (four hooks — **not** `secret-read-guard.sh`, which does not exist; see §1)
  at each `deny(...)`/`ask` emit point, called in addition to the existing stdout
  `hookSpecificOutput` emission, never replacing it. **Same change:**
  `hooks/log-session-cost.sh:75-81` gets a `kind != "decision"` (or equivalent `select(.cmd
  != null)`) filter before computing `rework_rounds`, and the `gates` metadata key published
  to auditctl (`:118-123`) either keeps decision rows inline (documented as a mixed array) or
  splits them into a sibling `decisions` key — pick one and document it in the same commit;
  the plan's original wording assumed the split already existed.
- **Tests:** new `hooks/tests/test-decision-row.sh`, modeled on `test-gate-log.sh`'s
  REQ-numbered oracle pattern, driving each of the four hooks via stdin exactly as the
  harness does and asserting the appended row shape. Extend `test-cost-hook-fields.sh` (or
  add a case) proving `rework_rounds` is unchanged by the presence of interleaved decision
  rows — this is the regression the §1 correction identifies.
- **CI check:** `hooks/tests/` already runs as part of agentops's local hook test suite; add
  the new script to whatever driver invokes the existing `test-*.sh` files.
- **Falsifier:** replay a session with both failed-then-retried gate commands and guard
  denials interleaved; if `rework_rounds` changes from its pre-slice value on that fixture,
  the filter is wrong and the slice is not done.

### Slice 3 — Standalone: context-economy gates and relocation (agentops, `#2435`)

- **Contract diff:** none yet — this slice is measurement before schema. Relocate
  `measure.py`, `classes.py`, `waste.py` from `outctl/studies/context-economy/` to
  `agentops/scripts/context_economy/` unchanged, then run them against the current stack to
  get real Bash-share and file-read-share numbers before deciding whether the 28%/45%
  thresholds the plan cites are still the right gates.
- **Files:** `agentops/scripts/context_economy/{measure,classes,waste}.py` (new location);
  `agentops/scripts/verify_quotes.py` (new, arm (b): `{claim, quote, ref}` verbatim
  whitespace-normalized check against `path:Lstart-Lend` or a spool file plus sha256,
  failures reported as `UNVERIFIED`, never dropped).
- **Tests:** a fixture corpus of known-verbatim and known-mismatched quotes for
  `verify_quotes.py`; no new tests needed for the relocated measurement scripts beyond
  confirming they still run against the current transcript format.
- **CI check:** none required to ship measurement; `verify_quotes.py` itself is the checker
  and should exit non-zero on any `UNVERIFIED` result if wired into a build step later.
- **Falsifier:** per the plan's own trial framing — if the mechanical check's accept rate on
  a fresh extraction run is far below the ~91% (393/432) already observed today, or if
  coordinator token savings and task quality (not yet measured) show no improvement over
  full-output archiving, retire arm (b).
- **Precondition this environment cannot verify:** `#2435`'s stated precondition is "outctl
  at `94840d7` is on devbox." `outctl` is not available in this run (§6); confirm the commit
  and source paths against the live devbox checkout before relocating.

### Slice 4 — Workstream C step 1: local-inference outcome rescore (local-inference, `#2436`)

Specification unchanged from the plan's §3 `exp-2026-09-local-inference-outcome-collateral`
experiment record — this run could not independently ground it (`local-inference` is not
available in this environment; see §6) and defers entirely to the plan's own file:line
citations (`exp-tier-effect.py:158`, `analyse-tier-effect.py`, `results-to-scorecard.py:28-29`,
`probe-escalation.sh`). Flagging only the parts a follow-up run with access should re-verify:

- **Contract diff:** pure derived `outcome ∈ {completed, completed_with_collateral_change,
  incomplete}` field, computed from existing `accepted`/`violated_constraint` booleans, plus
  `unavailable_reason = "no_violated_constraint_field"` for the 181 rows lacking the input.
- **Files:** `exp-tier-effect.py`, `analyse-tier-effect.py`, `results-to-scorecard.py`,
  `probe-escalation.sh` (backfill `task_id`/`arm` on the two escalation rows).
- **Tests:** none named in the source plan; a follow-up should confirm whether
  `local-inference` has an existing test suite these scripts fall under.
- **CI check:** none — this is an offline rescore of existing JSONL, zero model spend.
- **Falsifier:** as stated in the plan — retire the field if it surfaces zero
  `completed_with_collateral_change` rows, or if per-arm rankings are unchanged from the
  TAMPER free-text convention already in use.
- **Host note:** `#2436` records "host: local-inference is workstation-only" — this slice
  cannot run on devbox.

### Slice 5 — Workstream C step 2 (+ Workstream B): declared write scope on Release (sprintctl, `#2433`)

- **Contract diff:** add a `scope` (or `declared_scope`) field to `WorkRelease` — a set of
  path prefixes or glob patterns the release is authorized to touch — populated at reserve
  time. At Decision time, compute `collateral` = any changed path in the release's diff
  digest outside the declared scope, and store the resulting three-way `outcome` (reusing
  Slice 4's derivation, not a second implementation of the same enum) on the Decision's
  evidence digest. Vuoro projects `outcome`/`collateral` onto `EvidenceSet`/`ExperimentRecord`
  exports; per the plan and per §5.3 of the direction doc, it never computes or adjudicates
  the field itself — sprintctl (and its consumer, the harness that reports the diff) is the
  producer.
- **Workstream B fold-in:** the W2-1 change-memory scope note (log any claim-level
  supersession, derivation, or corroboration gap the reconstructing session needed as an
  `evidence_ref`'d observation) rides this item as one added scope note, per the agentops
  refine tick's own disposition (§0) — no separate host item, no separate code path.
- **Files:** `sprintctl/sprintctl/releases.py` (new field, normalization, digest inclusion);
  wherever the reserve-time CLI/served path accepts release parameters; a new pure function
  computing `collateral`/`outcome` from a diff digest and the declared scope.
- **Tests:** `tests/test_releases.py`, `tests/pg/test_releases.py` for the new field's
  normalization and digest stability; a new test module for the `collateral` derivation
  itself (unit-testable independent of storage).
- **CI check:** existing sprintctl release/decision test gate; no new CI job.
- **Falsifier:** condition to start, per the plan and per `#2433`'s own blocked-on note, is
  at least one Release with a declared scope and a diff digest actually produced through
  ordinary use — not a synthetic fixture. If after a reasonable trial period no real Release
  ever declares a scope narrower than "everything," the field adds nothing over the existing
  full diff and should be dropped.
- **Dependency, verified:** `#2433` is "blocked on S3 Release (ordering, not a gate)." S3 is
  substantially landed already — sprintctl's own git log shows `ca3fa92` (unbound
  query/schema 16, S3 PR5), `a3e02db` (Release trailer harvest, S3 PR4), `22f7afb` (served
  Decision/Release, S3 PR3) all merged before today's HEAD. The ordering dependency this
  slice cites is close to satisfied already; confirm the specific S3 sub-piece it needs
  (likely the served Release path, already present) before treating it as blocking.

## 3. Dependency order across repos

```
sprintctl S3 (landed: ca3fa92, a3e02db, 22f7afb)
        │
        ├─► Slice 1  Workstream A step 1  (sprintctl, #2432)         — no dependency, run now
        │
        ├─► Slice 5  Workstream C step 2 + B  (sprintctl, #2433)     — ordering dependency on S3, ~satisfied
        │
        └─► Workstream A step 2  (sprintctl, unfiled)                — needs a live sprint-559
              evidence-ref producer count first (TS-12); not startable until that
              measurement runs against live state, which this document cannot do

agentops (independent of sprintctl)
        │
        ├─► Slice 2  Workstream D step 1  (agentops, #2434)          — no dependency, run now
        │      │
        │      └─► Workstream D step 2  (W2-4, unfiled)              — reuses Slice 2's
        │             gate log and rework_rounds; must wait for Slice 2's kind-filter fix
        │             to land first, or it inherits the same corruption risk
        │
        └─► Slice 3  Standalone context-economy  (agentops, #2435)   — no dependency, run now
               (precondition: outctl@94840d7 present on devbox — unverified here)

local-inference (independent, workstation-only)
        │
        └─► Slice 4  Workstream C step 1  (local-inference, #2436)   — no dependency, run now
               its output (outcome-class derivation) is reused by Slice 5, not the reverse:
               Slice 5 should adopt the same three-way enum once Slice 4 has validated it,
               not invent a second definition

appservice (not available in this environment)
        │
        └─► W2-5 Delivery unit F                                     — appservice-owned,
               already specified in appservice's own plan, not started; agentops/vuoro
               involvement (an `_projects` ExperimentRecord comparing accept basis with F's
               verdict) cannot start until F completes two collection cycles

Workstream A steps 3-4, and W1-1/W1-5/W2-2 trigger-gated remainders:
    no slice — each waits on its own external trigger per the plan's §4, unchanged by this run
```

Everything above the appservice line and the trigger-gated remainder is runnable today, in
parallel, across three independent repos (sprintctl, agentops, local-inference), with only
two real cross-item orderings: Slice 5 waits (loosely) on sprintctl S3, and Workstream D
step 2 waits on Slice 2's `rework_rounds` fix.

## 4. Backlog items

| Title | Owner repo | Host item (if filed) | Acceptance criteria | Effort | Depends-on |
|---|---|---|---|---|---|
| `accepted_without_evidence` finding kind in `work.read.unbound` | sprintctl | `#2432` | New category returns correct alias-vs-explicit split against a live-state fixture with both accept paths represented; existing three categories unchanged; tests in `test_releases.py`, `test_decisions.py`, `pg/test_releases.py`, `test_served_decisions.py` pass | S | none |
| Guard-hook decision-row helper + `rework_rounds` kind filter | agentops | `#2434` | All four existing guard hooks (not `secret-read-guard.sh`) emit a decision row on deny/ask; `log-session-cost.sh`'s `rework_rounds` is provably unchanged by interleaved decision rows on a replay fixture; new `hooks/tests/test-decision-row.sh` passes | S | none |
| Context-economy Phase 1/2 gate measurement + relocation from outctl | agentops | `#2435` | `measure.py`/`classes.py`/`waste.py` running from `agentops/scripts/context_economy/` against the current stack; a real Bash-share and file-read-share number recorded (not the 28%/45% thresholds asserted untested); `verify_quotes.py` exists and correctly flags at least one known-mismatched quote as `UNVERIFIED` in its fixture | S-M | outctl@94840d7 present on devbox (unverified here) |
| Local-inference outcome-class rescore on escalation-probe data | local-inference | `#2436` | Both known tampered-and-accepted escalation rows surface under `completed_with_collateral_change`; malformed-JSONL count reported; per-arm rankings compared against the existing TAMPER-note convention | S | none |
| Declared write scope on Release, `collateral`/`outcome` derivation | sprintctl | `#2433` | At least one real (non-synthetic) Release carries a declared scope and a diff digest; `collateral` correctly flags a diff path outside the declared scope on a fixture; the Workstream B scope-note observation is recorded as part of the same item, not a separate code path; Vuoro-side export projects the field without computing it | M | sprintctl S3 (~satisfied); reuses Slice 4's outcome enum once validated |
| Evidence-obligations key on `acceptance_contract` + `unmet_obligations()` query | sprintctl | not yet filed | A live-state query confirms at least five sprint-559 items with ingested evidence refs before any schema change lands; if the count is below five, this item is replaced by a defer Decision recording the measurement, per TS-12 | S-M | live sprint-559 producer-count measurement (blocking, unresolved by this document) |
| `check_trajectory_flags.py`: gate-file weakening + rework-round trajectory flag | agentops | not yet filed | Flags test/gate-file weakening from `git diff base..head`; flags `rework_rounds >= 3` reusing Slice 2's corrected computation; runs locally at verify stage only (no CI gate, no session gate log on a runner); triggers one extra Sonnet review-synthesis pass, never a reject | S | Slice 2 (guard-hook decision rows + `rework_rounds` fix) must land first |
| `decisions_on_expired_evidence` read-time expiry join | sprintctl | not yet filed | Ported `expiry_of()` semantics join `work_decision.evidence_digests` to validity computed at read time; no stored expiry event, no new reason value, no gate | S | S4 evidence-home migration (not reached) |
| Delivery unit F: off-cluster collector, expiring receipt, assessor | appservice | appservice-owned, not sprintctl | Per appservice's own plan `2026-08-27-repository-assurance-and-operational-evidence.md`; out of this document's grounding scope | M | none stated in appservice's plan (unverified here) |
| `_projects` ExperimentRecord over Delivery unit F output | agentops | not yet filed | Compares F's accept-basis verdict against agentops's own acceptance record, after F completes two collection cycles | S | Delivery unit F (appservice), two collection cycles |

## 5. Conflicts and flags against the target state and non-goals

- **No conflicts found.** Every slice above is a derived projection or a narrow extension of
  an existing object (`Release`, `Decision`, the gate log), consistent with direction §5.3
  ("relations rather than objects") and with TS-5 (Decision is the sole terminal writer).
  None adds a queue, worker supervisor, model router, or execution control plane (the four
  things §11's "Explicitly deferred" and TS-2 rule out).
- **W1-4's full design correctly stays trigger-gated.** Its per-call obligation reference and
  expected-gain stopping require "the goal state changes to permit a Vuoro-owned worker
  supervisor" — itself a §11 non-goal today. Only the narrow `evidence_obligations` key and
  `unmet_obligations()` read query are in scope here, and even that is blocked on a
  measurement this document could not complete (see §4).
- **Workstream C step 2's "declared write scope" is new state on `WorkRelease`, not a new
  object** — it specializes the "bounded context and source references" field the direction
  doc (§4.1) already says a `WorkRelease` should carry. No conflict with §1.2's "no
  federation schema on speculation": the field is scoped to one already-accepted object and
  has a named condition to start (a real Release using it), not speculative schema.
- **Flag, not a conflict — an implementation risk surfaced by grounding, not by the plan:**
  Slice 2 (Workstream D step 1) will silently corrupt `rework_rounds` for every session after
  it ships unless the `kind != "decision"` filter lands in `log-session-cost.sh` in the same
  change. The plan's text assumed this partition already existed; it does not. This is now
  called out explicitly in §1 and §2 so it isn't rediscovered at implementation time.
  Workstream D step 2 (`check_trajectory_flags.py`), which reuses `rework_rounds`, inherits
  this risk if sequenced before the fix — §3's dependency graph orders it after Slice 2 for
  this reason.
  Flag it once at implementation time and once at review time; do not treat it as resolved
  merely because it is written down here.
- **Flag, informational:** `secret-read-guard.sh` does not exist. The plan's W1-2 disposition
  enumerates it as one of five hooks to instrument; only four exist. Building the fifth hook
  is separate, unscoped work this document does not specify — if it's wanted, it needs its
  own backlog item with its own acceptance criteria, not a silent fold into Slice 2.
- **Flag, informational:** the plan's citation of `decisions.py:135-150` as the existing fold
  point for W1-1's trigger-gated supersede rule names the general terminal-transition guard,
  not supersede-specific logic (§1). The disposition (trigger-gated, no code now) is
  unaffected, but revision 2 of the triage document should correct the citation so a future
  implementer doesn't go looking for logic that isn't there.

## 6. Repos not available in this environment

`appservice`, `local-inference`, `vuoro-evidence`, and `outctl` are not cloned in this
session and are outside its repository access scope. Every claim in this document about
those repos (Slice 3's `outctl@94840d7` precondition, Slice 4's local-inference file
citations, W2-5's appservice Delivery unit F) is carried over from the plan's own citations,
unverified here, and flagged as such at each point above. A follow-up run with access to
those four repos should re-ground those citations before implementation starts — the same
way this run re-grounded every sprintctl and agentops citation and found four material
corrections (the alias/explicit provenance proxy, the sprint-559 producer-count
unmeasurability, the missing `secret-read-guard.sh`, and the `rework_rounds` corruption risk).
