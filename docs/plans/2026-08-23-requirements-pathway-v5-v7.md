# Requirements pathway: v4 baseline → v7 full utilization

Status: **draft for owner ratification** (2026-08-23). Goal-oriented (KAOS-style) elicitation
from the end-state statement back to assigned requirements, with the delivery points, proving
points and utilization points that the implementation-planning and orchestration-planning
sessions will detail. This document authorizes nothing; it assigns.

Inputs: `2026-08-22-long-term-direction.md` (§1, §11, §13, §15), `2026-08-22-composition-v4-design-freeze.md`,
actionq `2026-08-20-tranche4-federation-storage-contract-freeze.md`, `2026-08-22-w4-authority-plane-rescope.md`,
`2026-08-22-w5-plus-backlog.md`; three read-only analyses run 2026-08-23 (vuoro-cloud 5.0
readiness; orchestrator/implementer tooling; workflow telemetry). Findings from those are cited
inline as `[A1]`, `[A2]`, `[A3]`.

Current baseline: actionq `fcf8259` / wheel **v0.1.28** (`b5f1d6fc…`), vuoro `ab00d29`
composition v4, reference profile revision `23cfc276…`; federation schema **uninitialized in
every environment** (verified read-only, actionq #45).

## 0. Owner constraints recorded 2026-08-23

- **C-1 Breaking changes are allowed.** The owner is the only consumer who can be hurt. This
  loosens every *compatibility* requirement below (catalog retention of `execution/v1`, W6
  compatibility facades, the epoch-less static-registry exception, "never silent" revision
  changes toward external consumers). It does **not** loosen requirements that exist for
  correctness: evidence binding, no-sole-attester, fail-closed identity, one-ledger-per-principal.
  Where C-1 applies, the item says `[C-1]`.
- **C-2 Hand-off shape.** After this pathway is ratified, work per gated release goes to a
  "dumb" orchestrator and a cheap implementer; the owner (or a frontier session) returns only at
  release completion or when a review escalates. This is dogfooding the §4.2 episodic-supervision
  model while the tooling is still being built, so the workflow itself is under measurement
  from day one (§6).

## 1. Goal graph

Notation: `G` goal, `R` requirement (leaf goal assigned to one agent), `E` expectation (leaf
assigned to the environment/operator), `O` obstacle, `A` agent. AND-refinement unless marked
OR. Each leaf names the delivery point (§4) it lands in.

```
G0  Vuoro is the governed distribution and control projection for a changing provider
    portfolio; work defined once, run by whichever qualified provider fits; intent,
    authority, evidence, recovery and decision continuity survive provider change.
│
├── G1  One authority for external execution references (federation plane), and no
│       legacy writer can contradict it.                                    [v5]
│   ├── G1.1 Every gateway-issued caller resolves to a minted principal that cannot
│   │        inherit a predecessor's ownership.
│   │   ├── R1.1.1 vuoro-cloud mints + persists a per-subject principal_epoch,
│   │   │          monotonic, fail-closed when absent.        A: vuoro-cloud   [A1]
│   │   ├── R1.1.2 principal_id composition is unambiguous (colon-safe subject
│   │   │          spelling or forbidden colons in sub).      A: vuoro-cloud + vuoro [A1 risk]
│   │   └── R1.1.3 reissued identity cannot inherit — both halves tested (5.5a).
│   │                                                          A: actionq
│   ├── G1.2 Federation schema is initialized by its own migration principal in each
│   │        environment, from the exact released wheel.
│   │   ├── E1.2.1 operator mints federation migration + runtime credentials (GitOps).
│   │   ├── R1.2.2 appservice carries federation role/grant/migrate manifests in the
│   │   │          vuoro-*-db pattern, Flux-applied.            A: appservice PR
│   │   └── R1.2.3 actionctl federation migrate|check-compatibility (DONE, 0.1.28).
│   ├── G1.3 federation.resource/v1 is bound, served and attested in the composition.
│   │   ├── R1.3.1 v4 rule 8 refined: filename→digest comparison, reject only
│   │   │          conflicting digests (frozen-rule amendment).   A: vuoro
│   │   ├── R1.3.2 second ActionQ provider record + adapter + closure + attestation
│   │   │          (5.4); validator still rejects one release unit backing both
│   │   │          exclusive capabilities.                         A: vuoro
│   │   └── R1.3.3 principal/v1 and grant/v1 recorded settled in the support manifest
│   │              with ownership_evidence; grant-scope falsifier wired (5.5). A: vuoro
│   ├── G1.4 Legacy writers are fenced by grants, not by routing.
│   │   ├── E1.4.1 operator captures pre-fence records 1–4 and post-fence 3–7 (5.9/5.11).
│   │   ├── R1.4.2 auditctl capture tooling for records 1–7 is green and bound
│   │   │          (captured_at, env, endpoint fp, revisions, actor, digest). A: auditctl
│   │   ├── E1.4.3 privilege fence executed (5.10); rollback rehearsed (5.12).
│   │   └── R1.4.4 backfill receipt gates the first federation.create grant per
│   │              environment (5.7).                             A: actionq + operator
│   └── G1.5 A restorable export exists before any fence (5.13).  E: operator
│
├── G2  A work release can be understood and settled from fresh context without the
│       originating session (falsifier 1).                              [v6]
│   ├── G2.1 Ledger objects exist as capability contracts owned by bound providers,
│   │        never a Vuoro store (direction §5.1, §15).
│   │   ├── R2.1.1 design freeze: schemas for WorkRelease, EffectGrant, EvidenceSet,
│   │   │          Decision, RecipeRevision, AgentProfileRevision + transitions.  A: vuoro
│   │   ├── R2.1.2 canonical store per object decided (sprintctl=WorkRelease default;
│   │   │          actionq federation=EffectGrant; EvidenceSet/Decision open).  D-5
│   │   └── R2.1.3 every contract names its lifecycle events and they are observed
│   │              in auditctl (falsifier 11).                     A: each provider
│   ├── G2.2 Shadow domains emit first-class events (Priority 1).   A: auditctl + providers
│   ├── G2.3 EvidenceSet with validity windows; EvidenceExpired (Priority 2).
│   ├── G2.4 WorkRelease + authority binding; supersession, stale completion,
│   │        fresh-context takeover (Priority 3).                  A: sprintctl
│   └── G2.5 EffectGrant runtime lifecycle over principal/v1 (Priority 4). A: actionq
│
├── G3  Frontier supervisors are episodic; deterministic systems poll and wait
│       (falsifier 2).                                                  [v7]
│   ├── G3.1 Unattended loop: packet → prepare → run → gate → receipt → PR → stop.
│   │   ├── R3.1.1 a driver chains hybrid_dispatch subcommands and opens the PR
│   │   │          (no `gh pr create` exists anywhere today).      A: agentops  [A2]
│   │   ├── R3.1.2 retry/escalation policy encoded, not prose; release-boundary
│   │   │          crossing is an encoded stop condition.          A: agentops  [A2]
│   │   └── R3.1.3 cheap-tier review record replaces the coordinator-review hard gate
│   │              for qualified action classes only; unqualified classes still
│   │              escalate (hybrid_dispatch.py:995 stays for those).  A: agentops
│   ├── G3.2 Cross-host authority reconciliation (Priority 5).      A: sprintctl
│   ├── G3.3 Decision correlation, AgentProfileRevision, experiment memory (Priority 6).
│   └── G3.4 Provider-neutral pilot through incumbent + minimization control +
│            one durable-workflow challenger (Priority 7; Restate candidate).  D-9
│
└── G4  The workflow that builds G1–G3 is measured, so the tooling is known to help
        or hurt (C-2).                                          [v4.1, cross-cutting]
    ├── R4.1 per-session cost + turns + tool calls + duration in the existing Stop
    │        hook; hook registered in every repo it claims to cover.   A: dev-env [A3]
    ├── R4.2 gate pass/fail, rework rounds and friction notes land in auditctl as
    │        workflow.* events.                                         A: dev-env [A3]
    ├── R4.3 per-release scorecard: frontier turns, cheap-tier first-pass rate,
    │        escalations, rework, cost; compared release over release.  A: agentops
    └── E4.4 OTel/Alloy deferred until a dashboard consumer exists (direction: no
             generic audit ingestion without a consumer).
```

### Obstacles (and the leaf that resolves each)

| O | Obstacle | Resolved by |
|---|---|---|
| O1 | Gateway path refuses every assertion today — `principal_epoch` is required by vuoro's resolver and never minted `[A1]` | R1.1.1 |
| O2 | Every real subject contains a colon (`github:123`, `connector:01K…`); right-parse of `issuer:sub:epoch` is ambiguous `[A1]` | R1.1.2 (decision D-2) |
| O3 | Migration creates tables only — no role, no GRANT; federation has no principal in dev | E1.2.1, R1.2.2 |
| O4 | Hand-applied cluster changes are pruned by Flux | R1.2.2 (GitOps only) |
| O5 | Three vuoro-service deployments on three digests — "deploy the composition" is ambiguous | D-4 |
| O6 | Rule 8 rejects two provider records naming one filename | R1.3.1 |
| O7 | Backfill and a native `federation.create` racing leaves unreconstructable provenance | R1.4.4 |
| O8 | No PR creation, no driver, no encoded escalation; candidate needs a human review JSON `[A2]` | R3.1.1–3 |
| O9 | Cheap-vs-expensive tier is unmeasured; first-attempt-pass baseline "unrecoverable" `[A2]` | R4.3 |
| O10 | Cost hook missing in actionq/agentops; per-host copies unmerged `[A3]` | R4.1 |
| O11 | Suspended execution migration jobs (v4, v5, v8-quiescence) misread as runs by a naive inventory | R1.4.2 (inventory reads status, not existence) |

## 2. Requirements traceability

Every leaf above originates in one of: direction §11 priority, freeze W-packet, rescope §7/§8,
backlog 5.x, or an analysis finding. The realignment pass (§7) adds a `pathway:` back-reference
to each source backlog item so the two cannot drift unnoticed.

## 3. Key decisions register

Decisions the owner must make or ratify. "Analysis" says what a further session would need
to do before the decision is safe; "Default" is what proceeds if the owner says nothing.

| D | Decision | Blocks | Default / recommendation | Analysis needed |
|---|---|---|---|---|
| D-1 | Federation gets its own migration + runtime principals (not execution's) | R1.2.2 | **Own principals** — freeze §217–275 and the `vuoro-*-db` secret-pair convention both say so | none; settled by text |
| D-2 | `principal_epoch` scope key and subject spelling: global-per-subject vs per-workspace; forbid colons vs re-spell `sub` as `users.id`/connector ULID; relax `sub == actor` on vuoro side | R1.1.1, R1.1.2 | **Global per subject; re-spell `sub` to the opaque id, keep `actor` as display; vuoro resolver checks `sub` against a new `subject` claim, not `actor`** `[C-1]` | vuoro-cloud session: write the migration + issuer change + conformance test proving composed id matches `MINTED_PRINCIPAL_ID` |
| D-3 | What is a reissue: does token rotation bump the epoch? | R1.1.1, G2.5 | **No** (owner's *soft* preference, 2026-08-23) — bump only on decommission-and-reuse and via an operator-only audited endpoint; never decrement. Rotation preserving actor keeps ownership. Overridable if the 5.0 session finds a materially better alternative; note it contradicts rescope §3 ("increments on any reissue"), which gets amended | same session as D-2 |
| D-4 | Which vuoro-service deployment is in scope for v5 | R1.2.2, 5.6 | **`vuoro-dev` only**; `vuoro-shared` and `agent-cockpit` follow in v5.1 after rollback rehearsal | appservice read: confirm which DB each deployment's DSN points at |
| D-5 | Canonical store per ledger object | G2.1 | sprintctl → WorkRelease, RecipeRevision; actionq federation → EffectGrant; auditctl → EvidenceSet, Decision; AgentProfileRevision → agentops repo (git-native) | ledger design session (direction §15) — **the v6 planning session** |
| D-6 | Rule 8 refinement ratified as v4 freeze Amendment | R1.3.1 | **Accept**: compare filename→digest, reject only conflicting digests | none; frozen-rule amendment recorded in the freeze's amendments section |
| D-7 | `[C-1]` Drop `execution/v1` from the served catalog in v5 instead of "later"; drop W6 compatibility facades; retire the epoch-less static-registry path once 5.0 lands | v5 scope, W6 | **Yes to all three**, as separately validated revisions (each still causes a global revision change and rediscovery proof) | none |
| D-8 | Qualification rule for cheap-tier action classes (which classes may self-promote to candidate without coordinator review) | R3.1.3 | Start with `mechanical_bulk` and docs-only; promote a class only after N≥5 first-pass green with zero escalation, measured by R4.3 | agentops session: define the classes and the promotion rule in the dispatch manifest |
| D-9 | First durable-workflow challenger and pilot envelope | G3.4 | Restate on appservice, pilot = one actionq review round reshaped to 4–8 parallel packets (memory: `orchestration-restate-pilot`) | orchestration-planning session; not before v6 |
| D-10 | W7 destructive retirement `[C-1]` | W7 | Still **not authorized** by this doc — C-1 removes the consumer reason, not the evidence reason; commission a separate short plan after v5's post-fence records pass | — |

D-1, D-6, D-7 need only a yes. D-2/D-3 need the vuoro-cloud session. D-5 and D-9 are the
two planning sessions this document hands off to.

## 4. Delivery points, proving points, utilization points

| Release | Enables | Scope (leaves) | Proving point (must be observed, not inferred) | Utilization point (what the owner actually does differently) |
|---|---|---|---|---|
| **v4.1** workflow telemetry (start now, no gate) | Knowing whether the tooling helps | R4.1, R4.2, R4.3 skeleton | First scorecard produced for the v5 release from hook data alone | Every release after v5 is compared against v5's numbers |
| **v5** federation bound and fenced (W5 complete) | One authority for external execution refs; EffectGrant foundation; gateway-issued callers work | G1 entire; D-1, D-2, D-3, D-4, D-6, D-7 | `check-compatibility` = compatible in dev from the 0.1.28 digest; post-fence records 3–7 pass; denial receipt from the *installed old wheel*; rollback rehearsed; gateway assertion accepted by vuoro resolver with a minted epoch | Owner stops running migrations by hand; federation is the only writer; `execution/v1` gone from catalog |
| **v6** ledger contracts (Priorities 1–4) | Fresh-context takeover; evidence with validity; grants with lifecycle | G2 entire; D-5 | Falsifier 1: a release settled from a cold session with no transcript; falsifier 11: every contract's events observed in auditctl; EvidenceExpired fired and honored | Owner hands a release to a cold session and it settles; sessions no longer re-derive state from chat history |
| **v7** episodic supervision + provider pilot (Priorities 5–7) | Full utilization: dumb orchestrator + cheap implementer run gated releases; frontier only at gates | G3 entire; D-8, D-9 | Falsifier 2: frontier turns per release drop ≥ 5× vs v5 scorecard; cheap-tier first-pass ≥ agreed threshold; one challenger passes the outcome suite | Owner returns only on completion or escalation — C-2 realized |

Sub-releases allowed inside each (v5.1 = shared/cockpit deployments, v6.x per ledger object).
Each sub-release is one orchestrator hand-off unit with its own gate set (§5).

## 5. Gate set per hand-off unit (what the dumb orchestrator runs)

Judgment-free, in order, all exist today `[A2]`:
1. `hybrid_dispatch.py prepare` cold gate from a disposable worktree at the pinned commit;
2. repo suite incl. `test_falsifier_coverage.py` (claim→falsifier→docstring triangle);
3. `verification/run_round_checks.py` (actionq) / equivalent per repo; untracked-file guard;
4. release contract digest validation; wheel or chart digest fetched, never trusted;
5. `hybrid_dispatch.py gate` → `receipt`; `candidate` only with a review record (D-8 decides
   when a cheap-tier record suffices);
6. **new (R3.1.1):** PR opened with receipt attached; stop. Merge is the owner's.

Stop conditions (encoded, R3.1.2): gate red twice on the same packet; release-boundary
crossing; any command outside `allowed_command_ids`; any path outside `writable_patch_paths`.

## 6. Workflow measurement (v4.1, starts before v5)

Minimal, extends what exists `[A3]`:
- Extend `.claude/hooks/log-session-cost.sh` with `turns`, `assistant_msgs`, `tool_calls`,
  `duration_s`; register the Stop hook in actionq and agentops (`AGENTS.md:167` currently
  over-claims coverage).
- New Stop hook: `auditctl add --type workflow.session --metadata {turns,cost_usd,gates[],
  rework_rounds,friction}`; `PostToolUse` matcher on test/gate commands appends pass/fail to a
  per-session scratch file the Stop hook drains.
- `/friction` skill → `auditctl add --type workflow.friction`.
- Scorecard script (R4.3) reads both sinks per release tag. OTel/Alloy deferred (E4.4).
- What "worse" looks like, so it is recognizable: rework rounds up, escalations up, or
  frontier turns flat while cost rises — any one for two consecutive releases.

## 7. Hand-offs produced by this assessment

| Doc | Repo | Consumer |
|---|---|---|
| this pathway | vuoro `docs/plans/` | owner ratification; both planning sessions |
| `2026-08-23-principal-epoch-backlog.md` | vuoro-cloud `docs/plans/` | 5.0 implementation session (D-2, D-3) |
| `2026-08-23-handoff-loop-and-telemetry.md` | agentops `docs/plans/agentops/` | orchestration-planning session (R3.1.x, R4.x, D-8) |
| realigned `w5-plus-backlog.md` | actionq `docs/plans/` | v5 implementation planning |
| appservice federation-db change | appservice PR | operator merge (E1.2.1, R1.2.2) |

Next sessions, in order: (1) owner answers D-1/D-6/D-7 and D-4; (2) v5 implementation
planning (actionq + appservice + vuoro-cloud backlogs → packets); (3) v6 ledger design freeze
(direction §15, D-5); (4) orchestration planning (agentops backlog, D-8, D-9). v4.1 is **not**
a session: telemetry (§6) is the first packet of (2), because v7's proving point is measured
against v5's numbers and v5 must therefore be the first measured release.
