---
doc_id: vuoro-versioning-backend-assessment-2026-10-21
title: Versioning backend for knowledge and agent definitions
purpose: proposal
lifecycle: draft
effective: 2026-10-21
subjects: [knowledge-workspace, knowledge-resolution]
related: [vuoro-knowledge-workspace-phase0, vuoro-knowledge-resolution, vuoro-accepted-outcome-learning, vuoro-agentic-private-workflow-2026-10-20]
---

# Versioning backend for knowledge and agent definitions

Status: draft proposal, 2026-10-21, assessed against Vuoro at the head of
`claude/ecstatic-albattani-ahk3vi`. Input: an external proposal to build a
gitoxide-based versioning backend that would "branch, compare, review, publish
and reconstruct the knowledge and definitions an agent operates with". This
record assesses it, strengthens it, submits it to an adversarial review, and
rebuilds it as the strongest concept that fits this estate. Nothing here is
ratified, changes a component disposition, or authorizes a migration.

**Recommendation.** Build the capability, not the engine. Vuoro versions
*compositions*: each component keeps its own authority (Git for repository
documentation, PostgreSQL for workspace collections, Sovite for agent and model
definitions), and Vuoro adds collection-level publications, candidate
selections, derived conflicts, typed diff, a context field on the run
manifest, and Git-readable export. No Git-authoritative store, no merge
engine, no new owner for agent definitions. Four increments, each behind its
own risk surface.

## 1. Optimistic assessment

The proposal's core concepts are right, and most of them already exist here
in some form. The value is in composing them, not in replacing them.

| Concept in the proposal | Why it is right | Already in the estate |
| --- | --- | --- |
| Typed object with durable identity, composed into immutable snapshots | A commit names one revision; an ID names the thing across revisions | `doc_id` (`vuoro-knowledge/v1`); `item_id`, `revision_id` ULIDs in `vuoro-workspace` |
| Git-authored sources separate from a Library Vuoro owns; cite, never mirror | One authority per collection | Phase 0 §2; `vuoro-knowledge` resolves Git docs; `vuoro-workspace` owns workspace collections |
| Proposal with expected base, rationale and evidence; publication is a separate decision | No lost updates, no silent semantic merge | `expected_base_revision_id`, `stale-base`, author cannot self-review, agents capped at contributor |
| Context snapshot: available, supplied and observed are different | Pinning sources is not enough; selection and truncation change inputs | `vuoro-knowledge-context/v1` manifest with digests, dirty flags and `recheck`; `RunManifest` in `vuoro-evidence` |
| Publish one root selection, not many moving refs | Multi-ref reads are not atomic | Release digests in Sprintctl; `manifest_digest` on the knowledge manifest |
| Restore creates a new publication; it never undoes effects, credentials or acceptance | History is append-only; effects are governed elsewhere | "Vuoro never fences external effects" (direction); roadmap Phase 3 deletion-survives-restore gate |
| A branch is not a privacy boundary | Git namespaces are not access control | Membership checked on every call; private Sovite bytes never enter Cloud |
| Retention is part of reconstructibility; report missing, never substitute | A digest does not bring content back | Export `missing` entries; knowledge-resolution §8 |
| Semantic diff as the primary review surface | Reviewers need "an agent gained a tool", not hunks | Not built; `recheck` is the seed |

The genuinely new capabilities, in order of value:

1. **Collection-level publication.** Today each item has one approved pointer.
   Nothing says "this set of approved revisions, as a whole, is the
   configuration that was reviewed". Restore and "what did the collection look
   like for run R" are reconstructions over timestamps.
2. **A run can cite exactly which knowledge it was given**, as one digest in
   the run manifest, so outcome comparison can distinguish runs that differ
   only in knowledge.
3. **Candidate selections** that agents can create without publishing, so a
   knowledge revision can be evaluated on accepted outcomes before a reviewer
   publishes it.
4. **Typed diff and derived conflicts**, so review compares what changed
   rather than text hunks, and overlapping proposals are visible before the
   second one fails with `stale-base`.
5. **Git-readable export** of a publication, giving the direction's
   portability promise a concrete form.

## 2. Where the proposal and the estate disagree

**Git as the authority for authored Library content.** Phase 0 chose
PostgreSQL for revisions, proposals and publication state; Git "is at most a
later read-only projection", and a Git projection "must not create a second
writable authority". The proposal's split (Git objects for content and
snapshots, PostgreSQL for current pointers) is the dual-write reconciliation
problem it warns about itself. Nothing in its capability list needs Git to be
the authority. Git's unique contribution is a portable, tool-readable
serialization, which is an export concern (roadmap Phase 4).

**Agent and environment definitions as Vuoro-versioned objects.** The private
workflow plan assigns configuration, admission, execution and receipts to
Sovite, with owned drafts, owner-only activation and qualification tied to the
exact revision. The direction says `RecipeRevision` and `AgentProfileRevision`
name comparison inputs and never compile or enforce. Vuoro therefore records
references to Sovite revisions and never owns them. The proposal's merge
example (one agent switches to a remote model, another widens data
classification, the text merges, the policy breaks) is Sovite's admission
problem, not a Vuoro merge rule.

**Three-way merge as a default stage.** Phase 0: "do not silently merge
semantic changes". The first increment surfaces overlap and requires a
resolution proposal against the current base. Per-type auto-merge is a later,
opt-in rule with its own evidence.

## 3. Adversarial review

A first baseline (publications in the store, a root context manifest owned by
"the work/evidence owner", typed `vuoro-knowledge/v1` metadata on workspace
revisions, author-declared proposal `touches`, Git export via the existing
import) was reviewed adversarially against the repository. The reviewer
verified the baseline's claims about what exists today and returned 17
findings. The decisive ones and how the rebuild answers them:

| # | Finding | Severity | Disposition |
| --- | --- | --- | --- |
| 1 | `context.compose` refusing on Sovite qualification makes Vuoro a second admission authority, and cannot run against a private Sovite. "Resolver output is not an execution permission." | blocking | Compose **records** the Sovite reference and the qualification state as reported in a Sovite receipt; it never evaluates. `recheck` reports when a later receipt invalidates it. |
| 2 | Requiring a Sprintctl Decision to qualify a publication conflates knowledge publication with work acceptance, which Phase 0 keeps separate, and leaks work-domain IDs to `knowledge:read`. | blocking | A publication is qualified by the workspace's own review rows and the publishing reviewer. A work Decision may be cited as an optional opaque reference, never required, never validated. |
| 3 | `publication.create` reading pointers then emitting can commit a publication citing an item tombstoned or re-pointed by a concurrent transaction. Only `member_set` locks `workspace_seq` before reading. | blocking | Lock `workspace_seq` before reading; record `snapshot_seq` in the publication and its digest. |
| 4 | CAS on `based_on_publication_id` prevents nothing useful and fails harmless cases; a replayed idempotency key after pointers moved returns a stale publication. | significant | CAS on `expected_snapshot_seq` or an explicit `(item, revision)` selection, included in the request so the idempotency digest changes. Lineage stays as lineage. |
| 5 | "Never purged while cited" is a cross-authority reference (knowledge schema reading evidence) and defeats deletion obligations. | blocking | Members cite `(item_id, revision_id, content_sha256)`. Purge removes bytes; every read of the publication reports the member as `missing` with its digest. Evidence retention is the evidence owner's concern. |
| 6 | "Withheld with digest" leaks tombstoned items to readers; the store has only workspace-wide roles, so tombstone is the only case. Restore that re-points at a `superseded` revision breaks `search approved`. | significant | No "withheld" concept. Tombstoned members are absent for readers (member count stated) and `missing` for contributors and above. Restore is a set of proposals, not a pointer move (§4.3). |
| 7 | Extending the full `vuoro-knowledge/v1` set to workspace revisions creates a second lifecycle and lets a workspace vote supersede a Git-ratified document. | significant | Workspace revisions carry `purpose`, `applies_to`, `consumption`, `related`, `supported_by` only; `lifecycle` derives from `state`. A workspace `supersedes` or `delegates` naming a repository document is always `proposed-supersession` or `delegation-not-authoritative`. Reading workspace items in the resolver is a separate catalog-composition increment. |
| 8 | `context.compose` has no owner in this repository, and a separate root manifest would not participate in `ExperimentRecord` comparisons, which select arms by `RunManifest` fields. No "materialized bundle" producer exists. | significant | The context manifest becomes named fields of `RunManifest`, covered by `composition_digest()`. Compose is a pure draft builder in `vuoro-evidence`, like the existing knowledge evidence draft; binding stays with Sprintctl. The bundle digest is an observed digest supplied by the harness, or absent. |
| 9 | Author-declared `touches` is gameable; overlap is derivable from stored rows. | significant | Conflicts derive from open proposals sharing `item_id` and from proposed revisions whose relation rows share a target. No authored field. |
| 10 | One item per proposal: multi-item changes cannot be reviewed atomically, and a publication can land between two halves of a supersession. | significant | `publication.create` validates relation closure over its selection before committing. Change-sets (several revisions, one review) are the named next step. |
| 11 | Export and import claims were wrong: import re-mints every ID into `origin_map`, ignores publication history, and export skips tombstoned items' revisions. Export and import are not served. `pygit2` is not locked; `git` is in the image. | significant | Export format bump with a `publications` section and `missing` members; import remaps membership through `origin_map`. Rendering uses the `git` CLI. Operator-only until the bundle-transfer design exists. |
| 12 | Semantic diff was specified over fields that do not exist and an unvalidated `sources` blob. `revision.diff` at `knowledge:read` could expose drafts. | significant | Diff v1: identity events, title, section-level text, relation rows. Metadata diff waits on finding 7 and a validated `sources` schema. Diff applies the same state gate as `read`. |
| 13 | The "first complete implementation" spans four coordinator-only risk surfaces and three external repositories. | significant | Split into four increments (§5). |
| 14 | Runs can cite only publications, so learning on a knowledge revision requires publishing it first; publication becomes the experiment lever. The gix proposal's unpublished branches covered this. | significant | Candidate selections: contributor-creatable, digest-bound, unpublished, never consulted by `latest-approved` reads. `recheck` reports selections that were never published or later rejected. |
| 15 | Offline authoring, history portability and private working sets were dropped silently. | minor | Deferred explicitly (§4.8). |
| 16 | Factual slips: Phase 0 is `proposed`, not operator-ratified; tombstone-survives-restore is a roadmap gate, not direction; import is operator-only; Sovite exposes a generation, not a documented revision digest. | minor | Corrected throughout. |
| 17 | Visibility of `proposal.conflicts` and `publication.diff` unstated. | minor | Stated in §4.2. |

Verified before accepting the review: `member_set` is the only mutation that
locks `workspace_seq` before reading (`store.py`); `delete` and
`proposal_review` do not; `import_workspace` calls `new_ulid()` for every
entity; `RunManifest` names harness, build, model and recipe; the service
image installs `git` and the lock has no `pygit2`; `vuoro.dispatch.json`
lists `knowledge-workspace-store-and-migrations`,
`service-compatibility-and-migrations` and `client-authority-boundary`.

## 4. The rebuilt concept

> Vuoro versions compositions of knowledge and definitions. Each component
> keeps its own authority. Vuoro adds publications and candidate selections to
> the workspace, derives conflicts and typed diffs from stored rows, names the
> knowledge composition on the run manifest, and renders publications as
> Git-readable exports. It evaluates nothing it does not own.

### 4.1 Object model

| Object | Owner | Status | Contents |
| --- | --- | --- | --- |
| Knowledge source revision | Git repository | existing | commit, path, blob, SHA-256, authored `vuoro-knowledge/v1` metadata |
| Workspace item revision | `vuoro-workspace` | existing, extended | Markdown, parent, state, reviewer; **new** validated `purpose`, `applies_to`, `consumption`, `related`, `supported_by`; `lifecycle` is derived from `state` and never authored |
| **Publication** | `vuoro-workspace` | new | `publication_id`, workspace, `snapshot_seq`, members `(item_id, revision_id, content_sha256)`, `based_on_publication_id` (lineage only), the approving proposal review rows it rests on, publishing principal, optional opaque `related_refs`, `publication_digest` over the canonical body |
| **Candidate selection** | `vuoro-workspace` | new | same shape as a publication, created by a contributor from any readable revisions (drafts included), digest-bound, never consulted by `latest-approved` reads or `search approved`; may name a `based_on_publication_id` |
| **Conflict** | `vuoro-workspace` | new, derived, rebuildable | open proposals that share an `item_id`, or whose proposed revisions' relation rows share a target; each with the diff of its revision against the common base |
| Agent and model revision | Sovite | existing, referenced | `installation_id`, `agent_id`, `generation`, qualification state **as stated in a Sovite receipt**, receipt digest; a revision digest if Sovite exposes one |
| Run manifest | `vuoro-evidence` | existing, extended | adds `knowledge_context_digest` (the `vuoro-knowledge-context/v1` manifest), `knowledge_selection` (publication or candidate selection id and digest), `definition_refs` (Sovite references), `supplied_context_digest` (observed, optional); all covered by `composition_digest()` |
| Supplied-context record | evidence path (Sprintctl) | existing path, new kind | what the harness reports it materialized, truncated, summarized or retrieved during the run; append-only per run; a `work.evidence.append-v1` item drafted by `vuoro-evidence` |

A publication is to a workspace what a release digest is to a work item: one
digest-bound selection at an exact sequence number. The per-item approved
pointer remains the live "latest approved" view. A run cites a publication or
a candidate selection; a reader wanting the newest content reads the pointers.

### 4.2 Operations

Additions to `knowledge.*`, served through the existing adapter protocol.
Every operation applies membership and the same state gate as `read`:
readers never see drafts, rejected revisions, or tombstoned members.

| Operation | Authority | Semantics |
| --- | --- | --- |
| `publication.create` | `knowledge:review` | lock `workspace_seq`; take either the current approved pointers or an explicit selection of approved revisions; refuse unless `expected_snapshot_seq` matches or the explicit selection is still approved; validate relation closure over the selection; write publication, event, outbox, audit; idempotency digest covers the selection or the expected sequence |
| `publication.read` | `knowledge:read` | by id or digest; members whose bytes were purged are `missing` with digest; tombstoned members are absent for readers and `missing` for contributors and above, with the member count always stated |
| `publication.diff` | `knowledge:read` | identity events (added, removed, re-revisioned, missing), title changes, section-level text changes, relation rows added or removed, lineage (`based_on`, restore-of) |
| `revision.diff` | `knowledge:read` | the same per item; full text diff on demand |
| `selection.create` | `knowledge:contribute` | digest-bound candidate selection over readable revisions; no pointer moves; no event visible to readers |
| `proposal.conflicts` | `knowledge:contribute` | derived overlaps for one proposal; exposes other contributors' open proposals, which contributors may already read |
| `publication.restore` | `knowledge:review` | for every member of the earlier publication whose revision is not the current approved one, create a revision copying its content with parent = current approved, and a proposal authored by the restoring principal; return the proposal set and a candidate selection naming the target. Review by another reviewer and `publication.create` complete the restore. Tombstoned members are reported, never resurrected. |
| `publication.export` | operator only (store API) | `vuoro-workspace-export/v2`: adds `publications` (members, `snapshot_seq`, lineage, `missing`) and renders a Git tree (§4.6); not served until the bundle-transfer design exists |

Not provided: merge, `touches`, Decision validation, any Sovite evaluation.

### 4.3 Invariants

1. One writable authority per collection. Git for repository docs,
   PostgreSQL for workspace items, publications and selections, Sovite for
   agent and model definitions. A Git rendering is derived and carries the
   publication digest it was rendered from.
2. A publication or selection is immutable and digest-bound, taken at an
   exact `snapshot_seq` under the sequence lock. Restore never mutates it.
3. Publication is qualified by workspace review alone. It does not deploy,
   accept work, or activate an agent.
4. Vuoro records definition references and qualification state from Sovite
   receipts; it never queries, evaluates or enforces Sovite policy.
5. Purge is decoupled from citation. Bytes go; the member becomes `missing`
   with its digest everywhere it is read. Deletion obligations survive
   restore and import.
6. Conflicts are derived from stored rows and surfaced, never auto-resolved.
7. A workspace item never supersedes or delegates for a repository document.
   Such a declaration is reported as proposed and ignored by the resolver.
8. Readers see nothing of tombstoned or unpublished content, in any
   operation, including diffs, conflicts and member lists.
9. Restore is a proposal; publication is a decision. The existing author
   cannot-review rule holds for restore proposals.

### 4.4 Typed diff

Diff v1 is computable from the current schema: identity events, title,
section-level text (using the existing section parser), relation rows.
Metadata diff (`purpose`, `applies_to`, `consumption`, sources re-pinned)
follows once the metadata columns and a validated `sources` schema exist.
Obligation diff ("this item became required for selector S") is a resolver
concern and belongs to the catalog-composition increment.

For Sovite references the diff reports only "generation N to M, qualification
state X to Y, receipt A to B". Content diff of definitions belongs to Sovite's
editors.

### 4.5 Context on the run manifest

`vuoro-evidence` gains a pure builder, modeled on the existing knowledge
evidence draft: given a knowledge-context manifest, a publication or selection
record, Sovite receipt references and an optional supplied-context digest, it
returns the `RunManifest` fields and a `work.evidence.append-v1` item draft.
Binding, chain position and idempotency stay with Sprintctl. A recheck helper
re-resolves each component and reports drift: knowledge sources (existing
`recheck`), selection superseded or never published, Sovite receipt
invalidated, harness build changed. It reports; the harness decides.

Because the fields sit on `RunManifest`, two runs that differ only in the
knowledge they were given have different composition digests and can be
compared as arms of an `ExperimentRecord`. Candidate selections make that
comparison possible before a reviewer publishes.

### 4.6 Git as interchange

An export renders one directory per member: `item.yaml` (stable ID, revision
ID, digest, validated metadata) and `content.md`, plus `publication.yaml`
(digest, `snapshot_seq`, lineage, `missing` members, origin map). It is
written with the `git` CLI already in the image. The commit message carries
the publication digest. Import is the existing operator path, extended to
remap publication members through `origin_map` and to record members absent
from the bundle as `missing`. No synchronization, no second writable copy.

### 4.7 Engine decision

| Need | Engine | Reason |
| --- | --- | --- |
| Workspace revisions, proposals, publications, selections, conflicts | PostgreSQL `knowledge` schema | already the authority; sequence lock, CAS, membership filter, FTS, idempotency and outbox exist |
| Repository documentation | Git through `vuoro-knowledge` | already the authority; manifests are digest-bound |
| Agent, model, environment revisions | Sovite | owns lifecycle, qualification and receipts |
| Export rendering | `git` CLI | present in the image; no Rust toolchain; `pygit2` not locked |
| gitoxide, Jujutsu, Dolt | not adopted | gix needs a Rust component that does not exist on the path; jj's operation log addresses whole-workspace rewriting, not a current requirement; Dolt is in the provisioning ledger as a work-ledger candidate only |

Revisit gix only if a Rust service appears on the export path, or if the
operator decides on a Git-authoritative migration for some workspace
collection, which Phase 0 requires as a separate decision.

### 4.8 Deferred, with reasons

- **Merge engine and per-type auto-merge.** After conflicts and typed diff
  have been used in review; needs evidence that silent merges are wanted.
- **Change-sets (several revisions, one review).** Needed for atomic
  multi-item changes; relation-closure validation on `publication.create`
  covers the correctness gap until then.
- **Offline or local authoring of workspace items.** The store requires a
  connection and membership on every call. The local-first path remains
  `vuoro-knowledge` over Git checkouts.
- **History portability.** Export carries one publication and its lineage
  references, not review notes or the full chain. Add when a customer needs
  to move a workspace, not a snapshot.
- **Private working sets.** Phase 0 has no private drafts; candidate
  selections are visible to contributors like drafts are.
- **Resolver over workspace items.** A catalog-composition change (served,
  authorized before catalog construction) with its own review.

## 5. Sequencing

| Increment | Repo and risk surface | Delivers | Exit evidence |
| --- | --- | --- | --- |
| V1 store | vuoro, `knowledge-workspace-store-and-migrations` (schema v2, new `vuoro-workspace` release) | publications, selections, derived conflicts, diff v1, restore-as-proposals, export v2 and Git rendering | store tests: sequence-lock race (delete and review during create), CAS on `expected_snapshot_seq`, idempotent replay after pointer move returns conflict, purged member reads `missing`, reader never sees tombstoned members in read, diff or conflicts, restore of a superseded member creates a proposal and not a pointer move, relation-closure refusal, export and import round trip through `origin_map` |
| V2 serve | vuoro, `service-compatibility-and-migrations` (pin bump, catalog revision, schema upgrade proofs); appservice migration Job | the new `knowledge.*` operations | catalog gate, handshake, upgrade proofs; export stays unserved |
| V3 evidence | vuoro `vuoro-evidence`; Sprintctl for the item kind | `RunManifest` fields, draft builder, recheck helper | two runs differing only in `knowledge_selection` have different composition digests and form distinct `ExperimentRecord` arms; recheck reports superseded selection and invalidated Sovite receipt |
| V4 resolver | vuoro catalog composition (coordinator-only) | resolver reads workspace items; metadata and obligation diff | A/B evaluation extension; workspace supersession of a Git document reported as proposed and ignored |

Acceptance scenario for V1 through V3 together:

> Two agents start from publication P and Sovite agent generation G. Each
> submits a proposal on the same item; `proposal.conflicts` shows the overlap
> with both diffs against P's revision. Agent B also creates candidate
> selection S containing its draft and runs against it; the run manifest
> names S and G's receipt. A reviewer rejects A's proposal; B's is approved by
> a second reviewer; `publication.create` with `expected_snapshot_seq` yields
> P2. Recheck of B's run reports "selection S now published as P2";
> recheck of A's run reports "publication P superseded by P2" and nothing
> else. `publication.restore(P)` returns one proposal and a candidate
> selection; after review it becomes P3, whose diff against P2 lists exactly
> that item. An administrator tombstones the item and purges it; P2 and P3
> read `missing` for contributors and omit it for readers with the member
> count stated. Export of P2 renders a Git tree and re-imports with new IDs,
> remapped members and the purged member recorded as `missing`. No run's
> evidence, receipt or acceptance changed.

## 6. Decisions needed from the owner

1. Confirm that publications and candidate selections belong in
   `vuoro-workspace` under Phase 0's PostgreSQL authority, and that no
   Git-authoritative migration is being opened.
2. Confirm that Vuoro records Sovite qualification from receipts only and
   never evaluates it, and ask Sovite whether it will expose a revision
   digest alongside the generation.
3. Accept that restore is a proposal set rather than a pointer move, which
   means a restore needs a second reviewer.
4. Approve the export format bump to `vuoro-workspace-export/v2` before the
   bundle-transfer design, on the understanding that export stays operator-only.
5. Decide whether V3's `RunManifest` extension waits for Sprintctl to accept
   the new evidence kind, or ships as a library change first.
