# Market integration milestone: end-to-end proof, evidence ingestion, current-state surface

Status: proposed plan (2026-10-05). It authorizes no build, deployment, repin or
publication. Agent-tooling claims it adds are `proposed` TS-17..TS-20 in
`agentops docs/plans/2026-09-17-target-state.md`; this document holds the
reasoning and the acceptance bar. It narrows nothing in TS-1, TS-2 or TS-16 and
adds no execution capability to Vuoro.

## 1. Basis and what is not established

Inputs: the 2026-10-05 landscape assessment (public repository records through
2026-10-04, the live MCP surface read on 2026-10-05, vendor documentation), and
the repository state cited below.

**Vendor facts are as reported by that assessment and were not re-verified in
this session.** Several products named in §2 are in preview or beta. Re-read the
primary source before any decision that depends on a vendor capability.

This is not an audit of the private Forgejo repositories or the deployed
cluster. A merged change is not a deployed change. Where this document says
"exists", it means the cited artifact exists in the repository.

## 2. Positioning against the market

The market now supplies execution, persistence, evaluation loops and
increasingly strong approval boundaries. Vuoro's remaining proposition is
narrower than "agent orchestration":

> Work stays resumable, and an accepted result stays reconstructible, across
> harnesses, providers, repositories and trust boundaries.

| Responsibility | Market option (per the assessment) | Vuoro stance |
|---|---|---|
| Repository maintenance, scheduled coding work | GitHub Agentic Workflows (public preview) | Baseline for Track A. Try it before building any dispatch machinery for GitHub-contained work. |
| Long-running hosted execution | Claude Managed Agents (beta) | Consume execution and session behaviour. Provider session IDs are references, never work identity. |
| Quality-driven iteration | Claude outcomes (separate grader, rubric) | Record the verdict as evidence. Do not reproduce the grader. |
| Durable workflow execution | Temporal | Adopt when a workload needs it. Not a reason to add a scheduler. |
| Stateful agent application logic | LangGraph | Useful inside an executor. Its graph state is not authority for released work. |
| Tool authorization | AWS AgentCore Policy | Integrate a policy provider where appropriate. Keep the protected-horizon contract. |
| Tracing, datasets, experiments | Langfuse | Expand the existing deployment (TS-15). No evaluation backend or trace UI. |

Two consequences:

1. Separating proposal from credentialed execution is no longer distinctive on
   its own. GitHub's safe-output architecture does it inside its workflow
   boundary. Vuoro's claim must be that the separation holds across GitHub,
   Forgejo, hosted sessions, local harnesses and customer-controlled execution.
2. Quality evaluation and authorization stay separate. A provider grader saying
   "satisfied" is evidence. It does not establish that the result matches the
   current release revision, that the evidence covers the exact artifact, or
   that the actor may authorize the effect.

## 3. One product, three surfaces, unchanged owners

This is packaging, not a merge of repositories or state machines.

| Surface | Responsibility | Owners (unchanged) |
|---|---|---|
| Work and evidence | Released intent, attempts, claims, evidence, decisions, continuation, reconstruction | Sprintctl (work and effect-intent authority); substrate hash chain for new hosted evidence (TS-6) |
| Connectors | MCP and thin observation/import adapters for native harnesses, provider events, PRs, evaluation results | vuoro-mcp-edge, vuoro-service; adapters admit through `vuoro-adapter-kit` |
| Protected operations | Customer-controlled acceptance and effect reconciliation; public clients propose, protected components authorize and apply | cred-broker/OpenBao, forge enforcement, GitOps reconciler |

TS-16's boundary is binding on all three: intent, coordination and evidence
cross; effects and credentials do not; there is no effect-apply scope.

## 4. Component dispositions

These follow the newer record (register v3 and its 2026-09-30 amendments), not
the older multi-product map. Status stays the register's to decide.

| Component | Disposition | Condition |
|---|---|---|
| ActionQ | Retire as a host; preserve the authority-plane abstraction | Provider-neutral contract and conformance tests live in Vuoro (§6 P3); implementations conform at their horizons. Lease/claim is not part of the freeze's contract (register `actionq`). |
| Auditctl | Preserve historical records and capture until the S4 import and recovery gate passes | Absorb evidence semantics without erasing provenance; original digests survive import (TS-6). |
| Kctl | Follow the planned transition into lesson/claim evidence plus Decisions (TS-13) | No new knowledge lifecycle during migration. |
| Operator-projection | Keep as the derived reading surface; it hosts the reconstruction view (§6 P1) | Cockpit is already retired. |
| Agentops | Operational guidance and narrow integration hooks | Execution, model selection and sandboxing stay native (TS-2). |
| Bindery | Real workload for continuation and evidence capture (§7 Track B) | Its game-specific strategy experiments stay independent and do not become Vuoro infrastructure. |
| Outctl, HostProto | Retired scope stays closed | |

## 5. State of the record this plan builds on (verified in-repo, 2026-10-05)

| Claim | Evidence |
|---|---|
| Lease, takeover, stale-result rejection with retention, restart and dependency release are exercised over the public route | `docs/evidence/2026-09-29-m1-4-settlement-scenario/` (local 34/34; live: every scenario case passed, one cleanup expectation failed on a harness environment gap, later corrected) |
| That scenario used two scripted processes, not two commercial harnesses | same README ("Callers") |
| A provider-neutral conformance suite already exists | `packages/vuoro-service/tests/conformance/`, see §6 P3 for the coverage matrix |
| Hosted evidence authority is the substrate hash chain; shards stay authoritative until verified import | long-term-direction §0.2 (2026-10-03); TS-6 |
| Strict metadata and a pure admission protocol merged without publishing, repinning or deploying | vuoro #177; mandatory catalog revision transport (#175) is still a proposal |
| Documentation fragmentation is real | the README's status table described the bootstrap stage; the estate map is a 2026-09-12 snapshot (addressed in §8) |

## 6. The four additions

Each lists what it is, what is explicitly not in it, and how it is accepted.
None adds an execution driver, scheduler, evaluation backend or timeline UI.

### P1. Reconstruction view (TS-17)

For one accepted result, show: released intent and revision; attempts and claims;
the exact artifact (digest); verification evidence; the accepting identity or
policy revision; the effect receipt; and every **missing link, named
explicitly**. It is a derived read in operator-projection over owner records.

Not: another activity timeline; a store; a second acceptance path.

Accepted when: for a settled item from the Track B run, the view answers "why was
this effect authorized?" with no field filled by inference, and a deliberately
removed link (for example an absent receipt) renders as `missing`, not as an
empty or omitted row.

### P2. Provider evaluation and event ingestion (TS-18)

Import Claude outcome results and GitHub workflow, PR and check results into the
existing evidence model. Each record captures: provider and build, session
reference, artifact digest, rubric or check revision, observed instruction
digest, and an assurance level. Anything the provider does not supply is
recorded as `unknown`; it is never defaulted.

This is the accepted "observe, do not compile profiles" direction (TS-3). It does
not resurrect `AgentProfileRevision` or `ExperimentRecord`. Model and
configuration comparisons use Langfuse on a small real case set; the results are
kept as evidence.

Not: a grader; an adapter that converts a provider verdict into an acceptance.
A provider verdict is an input to a Decision, never the Decision.

Accepted when: a provider verdict whose artifact digest differs from the
release's current artifact is recorded and **cannot** satisfy acceptance;
removing the integration leaves work identity, authority and history untouched.

### P3. Provider conformance suite (TS-19)

The suite exists; the work is to make its coverage explicit and close real gaps,
independent of any provider's storage layout. Current coverage in
`packages/vuoro-service/tests/conformance/`:

| Required behaviour | Covered by | Status |
|---|---|---|
| Stale claims; superseded report retained, not settled | `test_lease_contract.py::test_takeover_supersedes_old_heartbeat`, `::test_superseded_report_retained_without_settlement`, `::test_same_holder_cannot_resume_superseded_handle`; `test_oracle.py` (INV-L1 mutant) | covered |
| Exclusive claims; staleness has no background effect | `::test_live_lease_refuses_another_holder`, `::test_staleness_has_no_background_effect` | covered |
| Exact-digest acceptance distinct from settlement | `test_resource_contract.py::test_digest_bound_acceptance_is_distinct_from_local_settlement_and_supersession`, `::test_acceptance_rechecks_evidence_bytes_and_leaves_projection_unchanged` | covered |
| Revision conflicts leave state unchanged | `::test_identity_cas_rejection_does_not_change_projection_or_history`; owner observation `test_description_cas_preserves_aggregate_identity_and_refuses_stale_write` | covered |
| Replay and idempotency | `::test_command_first_binding_survives_response_loss_and_conflicting_digest` | covered for the resource aggregate; **not yet stated for provider-evidence ingestion (P2)** |
| Refusals at the public/protected boundary | `::test_actor_role_denial_is_durable_and_does_not_mutate`, `::test_creator_denial_and_nonowner_relation_are_nonmutating`; owner observation `test_reader_cannot_edit_and_stale_catalog_cannot_dispatch` | covered for role denial; **no case yet asserts that a public caller cannot reach a protected apply path** |
| Changed artifact cannot reuse approval | none stated as such | **gap** (this is the milestone's acceptance bar 2) |

Work: add the two gap cases and the P2 idempotency case, expressed against the
neutral contract, not a provider's storage. Gap assertions follow the existing
rule in `resource_owner_scenarios.py`: they must not count as satisfying an
invariant until an owner implements it.

### P4. Operational evidence completeness (TS-20)

Measure the proportion of relevant runs and effects that can actually be
reconstructed, and list the failures by class: capture failure, unattributed
work, inaccessible artifact, missing receipt. A perfect hash chain cannot explain
an event that never reached it; this metric is TS-16's control question made
measurable.

Not: a dashboard before there is a number. First deliverable is a repeatable
query and a baseline over the Track B run plus a sampled window of ordinary runs,
with the sample size stated beside every percentage.

## 7. The milestone: MI-1

One bounded milestone, two comparison tracks. Use real harnesses for Track B; the
scripted settlement scenario is the lower layer and stays green.

**Track A: ordinary repository work.** Compare the existing native-harness
workflow with GitHub Agentic Workflows on suitable GitHub repositories.
Measure: operator minutes, accepted-result quality, recovery effort, total cost.
Kill rule: if Agentic Workflows meets the need for GitHub-contained work, the
decision recorded is to use it and to build no further dispatch machinery for
that work.

**Track B: the differentiated Vuoro path.**

1. A hosted session produces a result.
2. A **different** native harness continues from durable records.
3. A protected verifier checks the exact artifact.
4. The authorized effect leaves a reconstructible receipt chain.
5. Introduce one interruption and one stale proposal.

Acceptance bar (all three required):

1. The successor continues without the predecessor's private conversation.
2. A stale or changed artifact cannot reuse approval.
3. Another reader can explain why the resulting effect was authorized, using the
   P1 view alone.

Bar 1 is TS-8's tripwire (a Codex or OpenCode session that cannot continue a
Claude handoff from the checkpoint alone means TS-8 is unmet). Bar 2 is the
conformance gap in §6 P3. Bar 3 is the P1 acceptance test.

No new Vuoro execution driver is required or permitted by this milestone.

## 8. Rollout rules and documentation changes

- Provider integrations are additive and preserve existing records. A failed
  integration is removable without moving work authority or rewriting history.
- Legacy Auditctl shards are not rewritten; import follows TS-6.
- New authority text is not added to superseded documents. The current-state
  entry point is the README status table, which now points here.
- Changes made with this plan: this document; README status table and surface
  map; long-term-direction header pointer; CHANGELOG entry; target-state
  TS-17..TS-20, path step and tripwires (agentops); product-positioning market
  section (vuoro-cloud `19-...`); one-line conformance and lesson-evidence
  pointers in actionq and kctl.

## 9. Falsifiers

- Track A shows Agentic Workflows equals or beats the native path on all four
  measures for GitHub-contained work: Vuoro adds no dispatch machinery there.
- Bar 1 fails: the continuation record is insufficient; fix the record, not the
  harness.
- P4's baseline shows reconstructability below the level where a reader can
  answer "why was this authorized" for most accepted effects: P1 is premature and
  capture (TS-16 E2 onward) is the work.
- A vendor ships a cross-provider, cross-boundary acceptance record. Nothing
  here assumes that will not happen: re-run §2 each quarter and supersede this
  document rather than editing it in place.
