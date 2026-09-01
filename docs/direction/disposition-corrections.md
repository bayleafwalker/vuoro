# Disposition register — corrections and actionable queue

Companion to `disposition-register.yaml` (v2, 2026-09-01). Generated from a 15-agent survey of
`/projects/dev`, each row verified at the artifact — code, schema, test, run record — rather than
at the plan describing it, then revised against an adversarial completeness pass.

**Review status: complete.** The critic returned `needs-revision` with 6 blocking/major gaps and 5
minor. All six were re-verified at the artifact and all six were real; the revision closed them and
corrected two v1 findings that were themselves wrong.

## Revision record

## What changed

All six critic gaps were verified at the artifact before any edit. **All six were real.** The register goes 46 → 49 items (v1 → v2). Validated YAML at `/tmp/claude-1000/-projects-dev--projects-vuoro-dispatch-ready/d017f176-3a62-4978-bd98-0cafe0390afc/scratchpad/register.yaml` — 49 items, 113 observations, 5 open_claims.

**1. `appservice` added (was blocking).** 262 commits in 14 days, HEAD `edf86bda` 2026-09-01 — the most active repo in the portfolio. Registered dispatch member, `adoption_level: dispatchable`, six action classes. Every outctl residue item the register schedules lives here: `outctl-pilot-rbac.yaml` present and wired at `kustomization.yaml:5`, the pilot kubeconfig present at 0600, and outctl in all three actors' `repo_ids` in `work-resource-observers.yaml`. v1 scheduled remediation into a repo it never enumerated. The outctl row now carries an explicit `residue_owner` pointing there.

**2. `verdict` vocabulary fixed (was blocking).** Added `verdict_vocabulary`; `verdict` now judges only the adjacent claim, and a separate `contradicts_record` flag carries disagreement with the decision record. All nine overloaded `contradicted` observations were rewritten as true statements — **zero observations now carry `contradicted`** (103 confirmed, 10 could-not-check). The worst case was sprintctl: "The resumability OUTCOME is served, tested and evidenced / verdict: contradicted" read at face value as *resume does not work*, the exact opposite of open_claim 1.

**3. `gitops-nixos` added.** 21 commits/14d, HEAD 2026-09-01. It wires the same `log-session-cost.sh` on the devbox (`hybrid-dispatch/claude-settings.json:14`) — a **second writer** into a log whose keys are exactly `ts, project, session, model, in, out, cost_usd`, with zero occurrences of `"host"` across 1537 rows. Per-host cost is unrecoverable for history.

**4. `bindery-core` demoted promote → hold.** ERH-006 is `status: pending`; the roadmap's own `ordering_rule` says "what remains for ERH-006 is a run, not a build". 12 of 12 authority decisions rejected. HEAD 2026-08-26. No observation evidenced a raise — `promote` now requires one by definition.

**5. Paths made absolute.** `/projects/dev/_artifacts/agentops/` has no `acceptance` directory (confirmed); the report lives only under `/projects/dev/agentops/_artifacts/`. Added `path_conventions` and a `path_warning` on open_claim 1.

**6. Meta-layer re-checked by concept — and the critic was partly wrong.** `_projects/takeover-20260820` is real, but it is **702 files, not 27, and it was executed, not unrun**: two sealed launch capsules, three recorded runs, a hash-verified blind review, and `FINAL-ADJUDICATION.md` returning **NARROW** with the direct-control path preferred on unblinding. Also, v1's absolute identifier claim is false — `render-fabric/docs/cross-repo-backlog.md:5` carries a metacoordinator framing. Net effect **strengthens** the retirement: an adjudicated negative result is harder to overturn than an absence. Added a `takeover-experiment` row (frozen) and a fifth open_claim.

Prose is compressed relative to v1 to fit one response; all verified facts and citations are retained.

## Critic gaps closed

- **[blocking]** MISSING ITEM: /projects/dev/appservice has no row, and it is the single most load-bearing repo in the portfolio. It is a registered dispatch member (appservice.dispatch.json, adoption_level dispatchable, six action classes enabled); it is the GitOps cluster repo that deploys vuoro-dev, vuoro-shared, vuoro-dev-db, vuoro-shared-db, actionq-db, sprintctl-postgres, cred-broker, agent-cockpit and homelab-analytics; it had 251 commits in the 14 days before the register date (131 on 2026-08-29..08-31 alone), making it by far the most active repo; and it is the #1 project in the cost log at 681 of ~1512 rows, which the register's own open_claim names as 'the largest real consumer at 30.2%'. Critically, every 'verify at the artifact' check the register performs lands inside appservice: I confirmed clusters/main/kubernetes/apps/gatus/app/outctl-pilot-rbac.yaml (referenced at kustomization.yaml:5), clusters/.kube/outctl-pilot-readonly.kubeconfig, and vuoro-shared/app/work-resource-observers.yaml carrying outctl in all three actors' repo_ids. So the outctl row's headline remediation — 'twelve live bindings survive across four repos and the cluster, including a credential and an RBAC object' — points into a repo the register does not enumerate, does not status, and assigns no owner or waking condition. A fresh agent has no row telling it the cluster exists, who owns it, or what its disposition is.
  - *Fix:* Add an `appservice` item: repos [/projects/dev/appservice], role 'Flux/Talos GitOps cluster repo — the deployment substrate for vuoro-shared, vuoro-dev, actionq-db, sprintctl-postgres, cred-broker and agent-cockpit', status active (251 commits/14d, deployed artifacts). Make it the owner of the outctl cluster-residue removal (gatus kustomization line 5, outctl-pilot-rbac.yaml, clusters/.kube/outctl-pilot-readonly.kubeconfig, the outctl repo_ids in work-resource-observers.yaml in both vuoro-dev and vuoro-shared), and cross-reference it from the outctl row and from the spend open_claim.

- **[blocking]** UNDEFINED, OVERLOADED `verdict` VOCABULARY. `status_vocabulary` is defined precisely; the `verdict` field on 91 observations is defined nowhere, and it is used in two incompatible senses in the same document. In most rows it means 'this claim is true' (vuoro-core: 'Deployed and current... verdict: confirmed'). In nine rows it means 'this contradicts the decision record' while the claim itself is true. The auditctl row proves the overload internally: observation 1 ('NO fixture partition exists') is `contradicted` and observation 2 ('record_class cannot carry the partition') is `confirmed`, and I verified both claims are true. The failure is worst on the highest-consequence row: sprintctl carries `claim: The resumability OUTCOME is served, tested and evidenced... verdict: contradicted`. Read at face value that says resume does NOT work — the exact opposite of the register's own first open_claim, which forbids scheduling work against a missing resume capability. I independently confirmed the claim is true (report-2026-08-29-after-deploy-repinned.md: PASS, aggregate 1.000, all eight hard gates). A third sense, `could-not-check`, is about the checking rather than the claim, and `open_claims` introduces a fourth undefined value, `partially-confirmed`.
  - *Fix:* Add a `verdict_vocabulary` block alongside `status_vocabulary` defining every value used (confirmed, contradicted, could-not-check, partially-confirmed). Then rewrite each observation so `verdict` refers to the adjacent `claim` and never to the decision record — e.g. restate sprintctl's second observation as `claim: The record's statement that session resume does not work is false; the resumability outcome is served, tested and evidenced` / `verdict: confirmed`. Apply the same to the nine `contradicted` observations (actionq placement, auditctl partition, browser-workbench, flowlab, acceptance-lab, hostproto x2, meta-layer).

- **[major]** MISSING ITEM: /projects/dev/gitops-nixos, an active infrastructure repo (20 commits in 14 days, HEAD 2026-08-30 'feat(dispatch): register the SessionStart binding hook for the devbox agent'). It is the NixOS provisioning for the devbox — the portfolio's second host — and it owns modules/system/hybrid-dispatch.nix, hybrid-dispatch/policy.json, hybrid-dispatch/claude-settings.json, agentops-client-tools.nix, modules/users/agentworker.nix and docs/runbooks/actionq-devbox-dispatch.md. It is therefore load-bearing for two register conclusions and named in neither. (1) The fourth open_claim reasons about cross-HOST continuity being 'supplied by NEITHER side' without naming the repo that defines the second host. (2) The cost open_claim analyses /projects/dev/.claude/session-costs.jsonl as if there were one writer, but gitops-nixos/modules/system/hybrid-dispatch/claude-settings.json:14 wires the same log-session-cost.sh hook on the devbox — a second producer into a log whose schema I confirmed has no host field (keys: ts, project, session, model, in, out, cost_usd). The remediation 'one shared reducer all three consumers import' does not account for a second host writing rows.
  - *Fix:* Add a `gitops-nixos` item: infrastructure, status active, role 'NixOS flake provisioning WorkstationLinux and the devbox agent host; owns the hybrid-dispatch policy, the agentworker user, and the devbox-side dispatch and cost hooks'. Reference it from the cross-host clause of open_claim 4 and from the cost open_claim as the second writer, and record that the missing host field is a schema defect in a log written from two hosts.

- **[major]** STATUS UPGRADED BEYOND ITS OWN EVIDENCE: bindery-core is the register's only `promote`, defined as 'Evidence supports raising it above its current role, but the raise has not been performed.' Its own row supplies the opposite. Its named gate ERH-006 is `status: pending` (I confirmed at docs/roadmap/post-ra2-hardening.yaml:52-54, with the file's own note at line 139 that 'what remains for ERH-006 is a run, not a build') — i.e. never run. Its divergence records that all 12 served-authority commands were rejected (I verified: 12 files in .sprintctl/authority-terminal-decisions, 12 with outcome rejected), that knowledge publication was blocked twice, and that two of the golden path's links (fresh-instance bootstrap, session resume) are absent. Its last commit is 2026-08-26. That is the definition of the register's `hold` — 'real work exists, paused behind a named checkable gate' — not `promote`. As written, the status is carrying the decision record's aspiration ('the intended primary real consumer for proving the Vuoro golden path end to end') rather than the observed state.
  - *Fix:* Demote bindery-core to `hold`, keeping ERH-006 verbatim as the named gate, or state explicitly in the row what raise the evidence supports and which observation supports it. If `promote` is retained, add an observation that actually evidences the raise; the two present observations evidence a local cycle and a 100%-negative Decision leg.

- **[major]** AMBIGUOUS PATH ON THE REGISTER'S MOST CONSEQUENTIAL CITATION. The sprintctl row and open_claim 1 cite `_artifacts/agentops/acceptance/resume-and-settle/report-2026-08-29-after-deploy-repinned.md` relative, while other rows cite `/projects/dev/_artifacts/agentops/audit/*.ndjson` and `ls /projects/dev/agentops/_artifacts/agentops` absolute. Two different `_artifacts` roots exist and both have an `agentops/` subtree. The report lives ONLY at /projects/dev/agentops/_artifacts/...; /projects/dev/_artifacts/agentops/ contains audit, capability, hybrid-dispatch, model, preflight and no `acceptance` directory at all (I confirmed the ls fails). A fresh agent resolving the relative path against the /projects/dev root — the natural reading, since neighbouring citations use it — finds nothing and would conclude the evidence for 'resume works' does not exist, reinstating exactly the claim the register is retiring. This is the same 'live trap' shape the register flags for outctl and vuoro-cloud's anchors doc.
  - *Fix:* Make every `source` path absolute. Specifically rewrite the sprintctl observation and open_claim 1 to /projects/dev/agentops/_artifacts/agentops/acceptance/resume-and-settle/report-2026-08-29-after-deploy-repinned.md, and add a note that /projects/dev/_artifacts and /projects/dev/agentops/_artifacts are distinct stores that both contain an agentops/ subtree.

- **[major]** META-LAYER ABSENCE CLAIM TESTED BY IDENTIFIER SPELLING, NOT BY CONCEPT, AND ITS LARGEST ARTIFACT IS UNCOVERED. The meta-layer row asserts (confidence high) that the layer 'was never built', 'exists as work-item records and one prose section', and that metaswarm/meta-swarm/meta_coordinator/MetaCoordinator 'appear in NO file of any type across /projects/dev'. The identifier grep is literally true — I reproduced it. But /projects/dev/_projects/takeover-20260820 is a 27-file coordinator-and-five-workers swarm package (00_EXECUTIVE_BRIEF, 01_ORCHESTRATOR_MASTER_PROMPT, 03_SWARM_TOPOLOGY_AND_PROTOCOL with a mermaid topology of one Coordinator over five Sol workers, 04_SYSTEM_AND_AUTHORITY_MODEL, 06_ACCEPTANCE_KILL_AND_NARROW_GATES, 09_REPOSITORY_WORKTREE_PLAN). It is exactly the described layer under different words, and it sits inside a directory the register does cover — but the project-instance row characterises /projects/dev/_projects as containing only 'host-local generated multi-repo work folders from materialize_project.py', and this directory has no .agentops-project-folder.json marker and is not generated. So the strongest surviving artifact of the retired layer is invisible to the register, and 'one prose section' understates it by 27 files.
  - *Fix:* Re-run the meta-layer absence check against concept terms (coordinator, swarm, orchestrator, fan-out, worker lane), not only the four camelCase identifiers, and record the corrected finding. Either give /projects/dev/_projects/takeover-20260820 its own row (frozen design package, unrun) or amend the project-instance row to state that _projects also holds non-generated directories that are not project instances, so a reader does not treat the marker-based instance count as the directory's full contents.

- **[minor]** FACTUAL ERROR PRODUCED BY THE APPSERVICE OMISSION: the cred-broker row states its 2026-08-30 commit is 'the most recent in the portfolio'. It is not — appservice had 131 commits between 2026-08-29 and 2026-09-01, including on 2026-08-30 and 2026-08-31, and cv-studio committed 2026-08-31. The claim is verdict `confirmed`, so a fresh agent would treat portfolio recency as settled when it was measured over an incomplete repo set.
  - *Fix:* Strike 'the most recent in the portfolio' from the cred-broker observation and keep only the checkable part (last commit 9b8ade8, 2026-08-30). Re-derive any recency ranking after appservice and gitops-nixos have rows.

- **[minor]** THREE UNFALSIFIABLE WAKING CONDITIONS. (a) hostproto-semantics-and-adapters: '...AND a consumer outside these repos needs the envelope' — no candidate named, no artifact to inspect, no date. (b) hostproto-a2a-worker: 'A named consumer actually needs HostProto delivered over A2A' — same defect, and it is the first of two conjuncts, so the whole condition is uncheckable. (c) cred-broker-public: 'Checkable substitute until one is written - a decision to publish or open-source the broker' — a decision that exists nowhere on disk is not checkable; the row itself concedes the condition is 'not written down anywhere'. These gate two `hold` items and one dormant repo, so an agent that checks the gate cannot report an answer.
  - *Fix:* Replace each with an artifact test. (a)/(b): 'a repository outside /projects/dev/hostproto-* declares a dependency on hostproto-semantics in its manifest' — greppable. (c): 'cred-broker-public gains a public remote (git remote -v resolves to a public host) or a LICENSE-bearing release tag', or state plainly that the condition is owner-only and mark the row's waking_condition as owner-actioned rather than agent-checkable.

- **[minor]** UNDATED RELATIVE DEADLINES. project-instance-runtime-envelope: 'Narrow or retire if instance count stays at two with no new instance materialized in a quarter.' datacluster: 'retire outright if datacluster-template remains the maintained copy through the next quarter.' Neither names a start date, so no later reader can determine whether the window has elapsed — the condition never becomes checkable.
  - *Fix:* Anchor both to the register's last_verified date: 'no new instance materialized between 2026-08-31 and 2026-11-30' and 'datacluster receives no non-sweep commit between 2026-08-31 and 2026-11-30'.

- **[minor]** CITED COMMANDS NOT REPRODUCIBLE AS WRITTEN. The browser-workbench row cites 'unittest discover -> Ran 34 tests OK' as the evidence for status active and for the inverted-premise correction. Run as written from the repo root it reports 'Ran 0 tests / NO TESTS RAN'; with -s tests it reports 'Ran 22 tests, FAILED (errors=4)'. The 34/34 result requires PYTHONPATH=src (documented at the repo's own HANDOFF.md:341, and I reproduced 'Ran 34 tests in 1.270s OK' with it). Similar shorthand appears elsewhere: 'core/model.py' is src/vuoro_evidence/core/model.py, and 'migrations/005_execution_groups.sql' is actionq/migrations/005_execution_groups.sql. A fresh agent re-running these to satisfy 'verify at the artifact, never at the report' gets a failure and would wrongly downgrade a correct finding.
  - *Fix:* Record every source command in runnable form with its working directory and environment: `cd /projects/dev/browser-workbench && PYTHONPATH=src python3 -m unittest discover -s tests`, and expand package-relative file citations to repo-relative paths.

- **[minor]** MISSING ITEM: /projects/dev/q-spec, an uncovered prose directory holding actionq-spec.md, sprintctl-spec.md, kctl-spec.md and dispatcher-spec.md — the original specs for four components the register does cover. It describes an `actionctl` CLI and a 'single deterministic dispatcher coordinator' that no longer exist (actionq-dispatcher is now a 34-line tombstone per the register's own row). It is not a git repo and was last touched 2026-07-18. This is the same live-trap shape the register flags for outctl and vuoro-cloud's anchors doc, and the register's own stated policy — 'this row exists because silent omission is the failure mode the register prevents' (work-release) — argues for covering it.
  - *Fix:* Add a `q-spec` item: not a repository, prose-only, status retired or frozen, role 'superseded v1 specs for actionq, sprintctl, kctl and the dispatcher', with an explicit note that its actionctl and dispatcher-coordinator vocabulary is against deleted surfaces and must not be used to resolve current behaviour.

## Corrections to the decision record

Where verified evidence contradicts the record. Not reconciled silently — the conflict is the finding.

### C1. The register need not enumerate /projects/dev/appservice.

**Evidence says:** appservice is the most active repo in the portfolio (262 commits in the 14 days to 2026-09-01, HEAD edf86bda), a registered dispatch member at adoption_level dispatchable with six action classes, the deployment substrate for vuoro-shared/vuoro-dev/actionq-db/sprintctl-postgres/cred-broker/agent-cockpit, and the physical location of every outctl cluster binding the register schedules for removal.

**Source:** `git -C /projects/dev/appservice log --since=2026-08-17 --oneline | wc -l -> 262; /projects/dev/appservice/appservice.dispatch.json; /projects/dev/appservice/clusters/main/kubernetes/apps/gatus/app/outctl-pilot-rbac.yaml and .../kustomization.yaml:5; /projects/dev/appservice/clusters/.kube/outctl-pilot-readonly.kubeconfig; .../vuoro-shared/app/work-resource-observers.yaml:9-11`

**Consequence:** New `appservice` row, status active, owning the outctl cluster-residue removal. The outctl row gains a `residue_owner` field pointing there so an agent acting on it goes to the right repo.

### C2. `verdict: contradicted` on an observation means the claim disagrees with the decision record.

**Evidence says:** The same field is used in two incompatible senses in one document. auditctl proves it internally: observation 1 ('NO fixture partition exists') was `contradicted` and observation 2 ('record_class cannot carry the partition') was `confirmed`, and both claims are true at the artifact. On sprintctl the overload inverted the meaning of the register's own highest-consequence finding.

**Source:** `internal to the v1 register; underlying claims re-verified this session (grep over /projects/dev/auditctl/auditctl/*.py; /projects/dev/agentops/_artifacts/agentops/acceptance/resume-and-settle/report-2026-08-29-after-deploy-repinned.md:4-28)`

**Consequence:** Added `verdict_vocabulary` defining confirmed / contradicted / partially-confirmed / could-not-check plus a separate `contradicts_record` flag. All nine observations rewritten as true statements; zero observations now carry `contradicted`.

### C3. The session-costs log has one producer, so a shared reducer across three consumers fixes the cost defect.

**Evidence says:** There are two producers on two hosts. gitops-nixos wires the same /projects/dev/agentops/templates/dispatch/hooks/log-session-cost.sh on the devbox, and the log carries no host field at all - keys are exactly ts, project, session, model, in, out, cost_usd, with `grep -c '"host"'` returning 0 across 1537 rows.

**Source:** `grep -rn 'log-session-cost' /projects/dev/gitops-nixos -> modules/system/hybrid-dispatch/claude-settings.json:14; head -1 /projects/dev/.claude/session-costs.jsonl parsed for keys; wc -l -> 1537`

**Consequence:** New `gitops-nixos` row (active). Added DEFECT 3 to the cost open_claim and a new block: no per-host cost figure can be derived, and this is unrecoverable for existing history rather than merely uncomputed.

### C4. bindery-core is `promote` - evidence supports raising it above its current role.

**Evidence says:** Its own evidence supports the opposite. ERH-006 is status pending and has never run; the roadmap's ordering_rule states 'what remains for ERH-006 is a run, not a build'. All 12 authority-terminal-decisions carry outcome rejected. Last commit 2026-08-26. Neither surviving observation evidences a raise.

**Source:** `/projects/dev/bindery-core/docs/roadmap/post-ra2-hardening.yaml (ERH-006 status pending; ordering_rule at ~line 139); ls .sprintctl/authority-terminal-decisions/ | wc -l -> 12, all outcome rejected; git log -1 -> aaae976 2026-08-26`

**Consequence:** Demoted to `hold` with ERH-006 kept verbatim as the named gate, plus a `status_change_note` recording why. The `promote` vocabulary entry now requires at least one observation evidencing the raise itself.

### C5. The acceptance report is at `_artifacts/agentops/acceptance/resume-and-settle/report-2026-08-29-after-deploy-repinned.md` (relative).

**Evidence says:** Two distinct _artifacts roots exist and both have an agentops/ subtree. /projects/dev/_artifacts/agentops/ holds audit, audit-retired-2026-08-26, capability, hybrid-dispatch, model, preflight - and no acceptance directory. The report exists only under /projects/dev/agentops/_artifacts/.

**Source:** `ls /projects/dev/_artifacts/agentops/ ; ls /projects/dev/_artifacts/agentops/acceptance -> No such file or directory; ls /projects/dev/agentops/_artifacts/agentops/acceptance/resume-and-settle/`

**Consequence:** All source paths made absolute (with ~/ abbreviating /projects/dev/). Added a `path_conventions` block naming the two roots and a `path_warning` on open_claim 1 - resolving the citation against the wrong root returns nothing and would reinstate the very claim being retired.

### C6. The meta-layer 'was never built'; it 'exists as work-item records and one prose section', and metaswarm/meta-swarm/meta_coordinator/MetaCoordinator appear in NO file of any type across /projects/dev.

**Evidence says:** Both halves are wrong, and the critic's own framing of the artifact ('27 files, unrun') is wrong too. /projects/dev/_projects/takeover-20260820 is 702 files, and it was EXECUTED: two sealed launch capsules (B2, CONTROL1), three recorded runs, a byte-preserved blind review with all six payload hashes passing, and results/FINAL-ADJUDICATION.md returning NARROW with the DIRECT CONTROL path preferred on unblinding. Separately the identifier grep returns three files, including /projects/dev/render-fabric/docs/cross-repo-backlog.md:5.

**Source:** `find /projects/dev/_projects/takeover-20260820 -type f | wc -l -> 702; results/FINAL-STATUS.json (verdict NARROW, review_preference_unblinded direct_control_path); results/GATE-STATUS-CURRENT.json (G0-G7 pass); 03_SWARM_TOPOLOGY_AND_PROTOCOL.md; grep -ril 'metaswarm|meta-swarm|meta_coordinator|MetaCoordinator' /projects/dev`

**Consequence:** New `takeover-experiment` row (frozen - closed with a verdict already reached). Meta-layer stays retired but on far stronger grounds: the shape was built as an experiment, run against a fair control, and lost. A third waking condition is added - overturning the NARROW verdict. A fifth open_claim records that absence-by-identifier-grep failed here and must be paired with a concept-level check.

## Actionable now

Small, verified, immediately executable. Each observed present at the artifact.

### A1. Delete the outctl pilot RBAC manifest and remove its line from the gatus kustomization.

- **Target:** `/projects/dev/appservice/clusters/main/kubernetes/apps/gatus/app/outctl-pilot-rbac.yaml; /projects/dev/appservice/clusters/main/kubernetes/apps/gatus/app/kustomization.yaml:5`
- **Why:** outctl was retired 2026-08-16 but this is a live cluster ingress lane. Re-verified present 2026-09-01. Owner is now the appservice row.

### A2. Delete the outctl pilot read-only kubeconfig.

- **Target:** `/projects/dev/appservice/clusters/.kube/outctl-pilot-readonly.kubeconfig`
- **Why:** A live credential (mode 0600) for a retired component. Re-verified present 2026-09-01.

### A3. Remove "outctl" AND "actionq-dispatcher" from the repo_ids list of all three actors (workstation-vuoro, devbox-agent-vuoro, agent-cockpit-vuoro) in both observer manifests.

- **Target:** `/projects/dev/appservice/clusters/main/kubernetes/apps/vuoro-shared/app/work-resource-observers.yaml:9-11; /projects/dev/appservice/clusters/main/kubernetes/apps/vuoro-dev/app/work-resource-observers.yaml:9-11`
- **Why:** Six live bindings to a retired repo plus six to a tombstone. Re-verified 2026-09-01; actionq-dispatcher's waking condition says it must keep shipping until this inventory is empty, so clearing it is a prerequisite for deleting the tombstone.

### A4. Add a `host` field to the emitted cost row (hostname or a NixOS-set identifier), and treat every existing row as host-unknown.

- **Target:** `/projects/dev/agentops/templates/dispatch/hooks/log-session-cost.sh; /projects/dev/gitops-nixos/modules/system/hybrid-dispatch/claude-settings.json:14`
- **Why:** Two hosts write /projects/dev/.claude/session-costs.jsonl and the schema has no host field - `grep -c '"host"'` returns 0 over 1537 rows. Without it no per-host figure is derivable, and cross-host continuity cannot be measured even in principle.

### A5. Fix summarizeCostLines to reduce to the newest row per session before aggregating - key on runtime_session_id||session, keep the row with the greatest [ts, cost_usd, out] tuple (the rule already in cost-summary.sh), then aggregate over survivors only.

- **Target:** `/projects/dev/agentops/apps/web/lib/cockpit/costs.js:43,45`
- **Why:** Rows are cumulative snapshots that supersede rather than accumulate. This one function is the 6.81x half of the cockpit's 15.13x overstatement; the correct reducer already exists twice in the same repo.

### A6. Change the assertions from 1.25 to 0.75 (both total_cost_usd and by_session).

- **Target:** `/projects/dev/agentops/apps/web/tests/cost-summary.test.js`
- **Why:** The test currently codifies the defect - it asserts that two cumulative snapshots of one session sum, which is exactly what AGENTS.md forbids. It must change in the same commit or it blocks the fix.

### A7. Replace the three-way opus/haiku/else price ladder with per-family branches, and emit cost_usd: null for an unmatched/unknown/<synthetic> model instead of silently applying Sonnet rates.

- **Target:** `/projects/dev/agentops/templates/dispatch/hooks/log-session-cost.sh:176-180 (and the price comment at :142-143)`
- **Why:** The opus arm bills exactly 3.0x current rates, the else-arm bills Sonnet 5 at 1.5x and understates Fable 5 by 3.33x, and 217 unpriceable rows are priced as Sonnet. This is the 2.21x half of the overstatement.

### A8. Extract the newest-per-session reduction into one shared module that cost-summary.sh, release_scorecard.py and costs.js all consume, and add a test that fails if any consumer sums raw rows.

- **Target:** `/projects/dev/agentops/templates/dispatch/hooks/cost-summary.sh; templates/dispatch/scripts/release_scorecard.py:38; apps/web/lib/cockpit/costs.js`
- **Why:** The rule was written into AGENTS.md and implemented twice on 2026-08-23; the third consumer was never migrated and its test asserted the wrong answer. Fixing the instance without the class lets it recur.

### A9. Add packages/vuoro-evidence to [tool.uv.workspace] members and [tool.pytest.ini_options] testpaths.

- **Target:** `/projects/dev/vuoro/pyproject.toml:15-22,24-33`
- **Why:** Re-verified 2026-09-01: still absent from both. The settlement-spine implementation (EvidenceSet, EffectGrant, Decision, reducer, boundary test) is not run by CI at all; its 16 tests pass only by hand. One line each.

### A10. Add a resolver-derived stream_class field (values live|fixture) to RESOLVED_CONTEXT_FIELDS, populate it in AuditContext.as_record() from an env var (default live) so tests set it and publishers cannot forge it, add a _migration_4 column, pass it through the two INSERTs, and default list/render to live.

- **Target:** `/projects/dev/auditctl/auditctl/validation.py:62; paths.py:39; db.py:76-85,143,178; cli.py`
- **Why:** One field, four files, one migration - and it is the hard precondition of the narrowed charter. It must NOT join ENVELOPE_FIELDS, which is validated all-or-nothing and would invalidate every historical event. Gates FlowLab, any event-unit study, and auditctl's own move to active.

### A11. Implement EvidenceSet and Decision as code in auditctl, and record either an adapter or an explicit supersession for bindery-core's Go definition.

- **Target:** `/projects/dev/auditctl/ (new schemas/ and model code); cross-reference /projects/dev/bindery-core/pkg/evidencev1/evidence.go:20-80`
- **Why:** Re-verified 2026-09-01: auditctl still has zero occurrences of EvidenceSet or evidence_set. Two incompatible implementations coexist with no adapter and no supersession; the named canonical home has no code. Highest-value open item in the register.

### A12. Delete the execution plane whose removal was decided on 2026-08-20 - application_enqueue.py, application_claim.py, application_dispatch.py, managed_dispatch.py, runner_auth.py - keeping federation.py, vuoro_federation.py and cas.py.

- **Target:** `/projects/dev/actionq/actionq/`
- **Why:** vuoro/docs/architecture/portable-execution.md is marked superseded 2026-08-20 and states ActionQ's target deletes the daemon, queue, leases, runner and fan-out engine. Decided and never executed; native harnesses now own queueing and subagent dispatch.

### A13. Drop 'placement' from any plan phrased as narrowing ActionQ, or schedule it explicitly as new build.

- **Target:** `planning documents citing ActionQ scope; evidence at /projects/dev/actionq/actionq/*.py`
- **Why:** Re-verified 2026-09-01: grep returns two hits, both the substring 'replacement' in deprecation metadata. execution_groups is deliberately not a placement engine - flat, immutable, failure_policy CHECK-constrained to one literal value.

### A14. Rewrite the three skills to the reservation/takeup API - task-pickup steps 3 and 7, sprint-resume, sprint-maintenance - and extend tests/test_docs_integrity.py to cover .agents/skills command validity.

- **Target:** `/projects/dev/sprintctl/.agents/skills/task-pickup/SKILL.md:26,37; sprint-resume/SKILL.md:21-24,39; sprint-maintenance/SKILL.md:26`
- **Why:** Three canonical skills teach commands that do not exist in 0.3.5 (HEAD 95e18f5) and tell agents to retain a claim_token that is no longer issued. The demoted bootstrap template's guidance is already correct - the live repo's is not.

### A15. Replace `sprintctl sprint current` in entry-checklist step 1 with a command that exists (the sprint group is backlog-seed/create/kind/list/show/status).

- **Target:** `/projects/dev/sprintctl-bootstrap-template/AGENTS.md:16-19`
- **Why:** Same drift class, in the repo whose only remaining job is to be a correct bootstrap example. Its step 2 (`reservation list --all`) is already correct.

### A16. Either destroy the vuoro-outctl-ready instance or repoint its canonical_project at a path that exists.

- **Target:** `/projects/dev/_projects/vuoro-outctl-ready/.agentops-project-folder.json (canonical_project: /tmp/outctl-materialize/agentops/project.toml)`
- **Why:** The recorded provenance path no longer exists, so the instance cannot be rebuilt from its own marker - the single property the project-folder mechanism exists to provide. It is also an instance for a retired component.

### A17. Clean the non-derived paths from the live instance root (.agents, .codex, .pytest_cache, .worktrees, actionq) and remove the stale outctl member so members/ matches the 7 declared binding members.

- **Target:** `/projects/dev/_projects/vuoro-dispatch-ready/ and its members/ directory`
- **Why:** materialize_project.py status currently refuses outright, so the instance cannot be validated, synced or destroyed cleanly. This is the concrete blocker on the envelope's own promote condition.

### A18. Delete the entire directory.

- **Target:** `/projects/dev/vuoro-bounded-output-starter`
- **Why:** Not a git repo (.git is an empty directory), superseded by /projects/dev/outctl, and it is the live trap that makes vuoro-cloud's 16-CURRENT-IMPLEMENTATION-ANCHORS.md read as stale. It also carries a second live outctl.dispatch.json.

### A19. Delete the two stale outctl dispatch manifests.

- **Target:** `/projects/dev/outctl/outctl.dispatch.json; /projects/dev/vuoro-bounded-output-starter/outctl.dispatch.json`
- **Why:** Both still declare adoption_level and enabled plan/build/review/verify/reconcile action classes for a repo declared 'not a project member' in agentops/docs/ecosystem.md:49.

### A20. Remove the Outctl area, the 'Outctl evidence envelope' work item, and the Outctl clauses from the ownership/lease/replay acceptance criteria.

- **Target:** `/projects/dev/vuoro/docs/plans/2026-08-15-vuoro-control-plane-work-items.yaml:46,172,278,841,1273`
- **Why:** An active plan still schedules work for a component retired 2026-08-16.

### A21. Retire the local dispatch/sprintctl opt-in - the repo advertises sprint hostproto#548 with items #2242-#2253 and README 'Status: Wave 0' while the upstream GitHub repo is archived.

- **Target:** `/projects/dev/hostproto/hostproto.dispatch.json; /projects/dev/hostproto/README.md`
- **Why:** A retired repo presenting itself to dispatch as active Wave 1 delivery work is exactly how a retired line gets double-counted as active.

### A22. Replace the retired placeholder extension URI with the canonical GitHub Pages $id used by the rest of the family.

- **Target:** `/projects/dev/hostproto-a2a-worker/src/a2a.ts:6 (EXTENSION_URI = 'https://hostproto.invalid/a2a/work-order/v1')`
- **Why:** One-line fix; the rest of the HostProto family already migrated, and this is the only remaining .invalid reference - it ships in the agent card.

### A23. Refresh or delete the stale verification context - it names 'pinned-sprintctl-0.2.14-operation-catalog' as source of truth and asserts the invariant 'project-next-work-explain-is-not-added', while the pinned 0.3.5 adapter does expose work.project.next-work-explain.

- **Target:** `/projects/dev/vuoro/verification/contexts/maintenance-capability-served.json (against packages/vuoro-service/composition/adapter-pins.json and sprintctl vuoro_adapter.py:830)`
- **Why:** A verification context that has drifted from the composition it constrains will either pass vacuously or fail for the wrong reason.

## Queue outcomes (2026-09-01)

Executed against the v2 queue. Landed at the canonical remote unless noted.

| Item | Outcome | Where |
| --- | --- | --- |
| A1–A3 outctl cluster residue | done | appservice `c147022f`, repaired by `4ef1b38b` |
| A4 host field on cost rows | done | agentops `63725a7` |
| A5–A8 cost repair (15.13×) | done | agentops `63725a7` |
| A9 vuoro-evidence into CI | done | vuoro `01725a2` |
| A10 fixture partition at ingestion | done | auditctl `0305f65` |
| A11 EvidenceSet / Decision | done | contract auditctl `7772aac`; `record_class` relaxed by owner ruling, auditctl `cba8850` |
| A12 delete ActionQ execution plane | **re-expressed, not executed** | actionq `014e12b`; the repo's own tranche plan supersedes the item's list |
| A13 drop "placement" from ActionQ scope | **stale** | no live document says it |
| A14 sprintctl skills + drift guard | done | sprintctl `23f8a68` |
| A15 bootstrap template commands | done | `8026f6d`, `ea8a33e` |
| A16 vuoro-outctl-ready instance | repointed; destroy blocked | marker now resolves; tool refuses on binding drift |
| A17 clean live instance root | done (destroy still blocked) | both paths were redundant; real work was in `members/outctl`, rescued as outctl `f1032fd` |
| A18 delete vuoro-bounded-output-starter | done | supersession total by blob and symbol scan; directory deleted |
| A19 stale outctl dispatch manifests | done | outctl `e743e16`, pushed; second target removed with the starter |
| A20 outctl in control-plane work items | **stale** | that plan is already `status: superseded` |
| A21 hostproto forward framing | done, unpushed | `6bd1ca2`; upstream archived, read-only |
| A22 `.invalid` extension URI | done | hostproto-a2a-worker `6f978b7` |
| A23 drifted verification context | done | vuoro `01725a2` |

Four items were refused rather than forced, each because the artifact
contradicted the item's premise. That is the register working as intended: an
item is a hypothesis about the repository, and the repository gets the last word.

**Rescued in passing:** destroying `_projects/vuoro-outctl-ready` would have
deleted `EVIDENCE_FREQUENCY_COUNT_2026-08-16.md`, the count behind outctl's kill
decision, untracked and present in no git history anywhere. Now committed to the
outctl repository. The queue assumed disposable instances hold nothing unique.

## Method correction (2026-09-01)

Two of the four refusals above were themselves premised on the wrong test.

`diff -rq` answers *are these two trees identical*. That is not the disposal
question. The disposal question is *does this content exist anywhere else*, and
only object-store membership answers it — `git hash-object` on each file, then
`git cat-file -e` against the candidate repository. Run that way, the
vuoro-bounded-output-starter tree reduced from "many differing files and three
unique paths" to five files, three of them stubs whose every symbol survives in
their successor.

The same mistake was available on `.worktrees/p31-db2`, where a commit SHA absent
from the parent repository looked like unmerged work. It was a squash-merge: the
commit object is gone, its *tree* is byte-identical to one on `origin/main`. Compare
trees, not commit identities.

Both are the same error in different clothing — comparing containers when the
question is about contents. It is available on every remaining row, so check
membership before recording anything as unique.

## Rescues (2026-09-01)

Two files-that-existed-nowhere-else were found while executing this queue, both
inside directories the queue described as disposable:

1. `EVIDENCE_FREQUENCY_COUNT_2026-08-16.md` — the count behind outctl's own
   retirement decision, untracked in `_projects/vuoro-outctl-ready`.
2. A 1110-line observability module with tests, three schemas, docs and examples —
   uncommitted in `_projects/vuoro-dispatch-ready/members/outctl`.

Both are now committed and pushed to `github.com/bayleafwalker/outctl`, which is
not archived. The lesson is not "check harder"; it is that a disposable instance is
disposable only once what it holds is provably not the only copy, and the check
that establishes that is the membership scan above.

## Open follow-up: abandoned workloads (2026-09-01)

Removing `appservice/clusters/main/kubernetes/apps/actionq-server/` did not remove
the workload. `prune: true` collects what a **live** Kustomization owns; deleting
the Kustomization removes the owner, and its objects are abandoned rather than
collected. The Deployment kept serving for 116 days, still labelled
`kustomize.toolkit.fluxcd.io/name: actionq-server` for a Kustomization that no
longer existed, and nothing reported it.

That failure mode is not specific to this app. Any directory removed from
`apps/` the same way has probably left its objects running unowned. The sweep is
cheap — compare live objects carrying `kustomize.toolkit.fluxcd.io/name` labels
against the set of Kustomizations that actually exist — and it has not been done.

This is the durable lesson from the whole queue, in its third form: a status that
describes a thing is not the thing. Git said the app was retired, the plan said the
gate was a decision, and the pod was up the entire time.
