---
doc_id: vuoro-knowledge-resolution
title: Knowledge resolution and provenance
purpose: proposal
lifecycle: proposed
effective: 2026-10-05
applies_to:
  components: [vuoro-core, kctl, knowledge-base]
subjects: [knowledge-resolution]
---

# Knowledge resolution and provenance

Status: **proposed**, 2026-10-05. This document proposes a capability and
describes the first increment that implements it (`packages/vuoro-knowledge`).
It does not ratify itself, change any component's disposition, or revive
Kctl's product boundary. Human ratification stays with the process that owns
it.

> **Vuoro resolves and records the knowledge applicable to a piece of work; it
> does not replace the repositories where that knowledge is authored.**

## 1. The problem, from this estate

Ordinary retrieval cannot answer the questions agents actually get wrong. Three
real records about knowledge management answer three different questions:

| Question | Record | What it establishes |
| --- | --- | --- |
| What was approved? | `kctl/docs/plans/vuoro-served-knowledge-alignment.md`, `status: ratified`, 2026-07-21 | Kctl owns candidate, review, supersession and publication-reference semantics; Git keeps document content. |
| What is the intended destination? | `vuoro/docs/direction/disposition-register.yaml` v3, 2026-09-17 | `kctl` and `knowledge-base` are `retiring` (D1, TS-13). |
| Has the migration happened? | `agentops/docs/plans/2026-10-03-s4-evidence-home-preparation.md` | Nothing: it says TS-13 "remains a proposed follow-on … not a retirement performed by this note". Completion needs implementation or operational evidence. |

A search hit that says "ratified" gives the wrong answer (extend Kctl). So does
"take the newest document" (a proposal). The register does not supersede the
July plan; it changes the disposition of the component the plan governs. The
capability has to keep these apart and say so, so a new session does not have
to work out the decision history again.

The estate already holds the key idea. `docs/architecture/agentic-estate.md`
assigns authority by question: "the register decides status and this record
decides shape". The contract below generalizes that.

## 2. What this adds over well-kept Git documentation

The baseline is Git documentation with review, CODEOWNERS, an index and
validated frontmatter, reachable over the GitHub MCP server. Not a pile of
unstructured Markdown.

| Capability | Value |
| --- | --- |
| Search and read Markdown over MCP | Convenience only; GitHub already provides it. Not a reason to build anything. |
| Resolve the **applicable, authoritative** sources across repositories | Real. Needs interpreted metadata and relationships, not text matching. |
| **Bind** the exact sources supplied to a work result | The Vuoro-specific part. It extends the existing goal: accepted results can be reconstructed. |

So the comparison is *Git docs + conventions* against *the same docs + validated
semantics, shared resolution, and work-context binding*. Backstage TechDocs
plus its catalog is the precedent (docs live with code; a catalog adds identity,
ownership and typed relations). We take the pattern, not the platform.

## 3. Ownership

| Concern | Owner | Notes |
| --- | --- | --- |
| Authored content and authored metadata | Each document's own Git repository | Frontmatter, or that repository's `knowledge.toml` sidecar. Changes go through normal review. |
| Catalog and search index | Derived, rebuildable projection | Same checkouts give the same `catalog.digest`. Deleting the catalog loses nothing. |
| Which sources were supplied to which work | The existing work/evidence authority (Sprintctl) | Recorded as an evidence reference. No new store. |
| Component status | The delegated status authority (today the disposition register) | The resolver reads it and never sets it. |
| Ratification | The owning human/process gate | A `lifecycle: ratified` field is a claim. The resolver never treats it as proof. |

Fit with the current direction: Kctl and `knowledge-base` are `retiring`.
Their useful semantics (supersession, provenance, content digests, Git as
canonical content) carry over into document metadata and the evidence path.
Their review workflow, candidate store and publication state machine do not.
This capability adds no work, review, approval or publication state machine. It
is a library and CLI inside `vuoro-core`, and its outputs flow through owners
that already exist.

## 4. Metadata contract (`vuoro-knowledge/v1`)

Metadata earns its place only if it changes what the resolver selects,
excludes, warns about or records. A document opts in by declaring `doc_id`. It
becomes fully classifiable once it also declares `purpose`. Nothing else is
required.

| Field | Enables |
| --- | --- |
| `doc_id`, `subjects` | Identity that survives a file move; subjects are cross-repository entities declared in `knowledge.toml` (`[[subjects]] id, components`). |
| `purpose`: `decision · specification · runbook · explanation · observation · proposal` | A proposal is never served as a procedure; an observation is never served as current state. |
| `lifecycle`: `draft · proposed · ratified · superseded · withdrawn` | Separates guidance in force from suggestions. Legacy `status:` maps only where the mapping needs no judgement (the five same-named values); every other value is reported as unmapped. |
| `applies_to`: `repos`, `components`, `environments`, `versions` (`{component: ">=0.1.40,<0.1.50"}`) | Guidance for one deployment or software generation is excluded for another, and the result says why. If a document is constrained on a dimension the work context does not state, that is disclosed as unchecked. |
| `effective`, `ratified_at`, `observed`, `verified` | A recent edit is not a recent verification. Observations carry their age relative to `as_of`. |
| `supersedes` (`doc_id`, `doc_id:fragment`, or `{doc, in_part, scope}`), `implements`, `supported_by`, `related`, `superseded_by` (back-reference, checked for agreement) | Explicit supersession and traceability. |
| `delegates: [{question, source, scope}]` | Question-scoped authority (§5). |
| `establishes: [{component, state}]` (observations only) | Evidence that a transition actually happened. |
| `facts` (`status-register/v1`) on a structured register | Per-component status read from the register itself, not copied out of it. |
| `maintainer` (alias `owner`) | Maintenance contact. Not authorization. |

Three separations are deliberate and must not collapse into one `current` flag:

* **Document status** (lifecycle). A document can be ratified and still
  describe a future system.
* **Component status**, owned by the status authority. A component can be
  `retiring` and still running.
* **Evidence status**: observations and receipts. An observation can be
  accurate on 2026-09-12 and unreliable on 2026-10-05.

Three kinds of metadata are kept apart:

* **Authored** (frontmatter or `knowledge.toml`): identity, purpose,
  applicability, governing references, supersession, maintainer.
* **Derived** (catalog): headings and section line ranges, links, content
  SHA-256, Git commit and blob, dirty state.
* **Operational** (work/evidence store): which manifest was supplied to which
  work item, and what was accepted afterwards.

A document must not carry both frontmatter and a sidecar entry. The validator
rejects this. Ratified records are annotated through the sidecar so their bytes
stay untouched.

## 5. Authority is scoped to a question

There is no universal ranking. Newer does not beat older, and `decision` does
not beat `observation`. Precedence exists only where something has declared it:

* A delegation counts only when the declaring document is authoritative
  (ratified and not fully superseded), or when the delegation sits in the
  repository's reviewed `knowledge.toml`. A document cannot appoint itself.
  A proposal that "delegates" is reported (`delegation-not-authoritative`) and
  ignored.
* The same rule applies to supersession. A proposal that declares
  `supersedes` only proposes it (`proposed-supersession`). The target stays in
  force and the result shows the pending proposal.
* When two effective delegations name different sources for overlapping scope,
  validation fails (`conflicting-delegation`) and resolution returns
  `authority-conflict` without choosing one.
* When a question the caller marks as required has no delegation, the result
  is `no-authority`. The resolver never substitutes a plausible document.

## 6. Structure: organize around questions, not folders

* **Reader structure.** Diátaxis distinguishes tutorials, how-to, reference
  and explanation. To that we add decision records and point-in-time evidence.
  In practice there are three categories: **guidance** (what to follow now, in
  a stated context), **records** (what was decided, proposed, observed or
  learned at a time) and **reference** (exact contracts and schemas). They link
  to each other but are never presented as interchangeable. A current guide
  can summarize the applicable decision and link to its history. A historical
  proposal stays discoverable but is never default guidance.
* **Entity structure.** Subjects such as `knowledge-resolution` or
  `evidence-home` span repositories. Views by repository, capability or
  operator task are generated from metadata. The folder a document lives in
  does not have to match every way of navigating to it.
* **In-document structure.** Use predictable sections for each purpose, so a
  bounded read (`get --section verification`) still arrives with its
  applicability notices:
  * runbook: applicability, prerequisites, actions, verification, failure handling;
  * decision: decision, scope, rationale, rejected alternatives, supersession;
  * observation/survey: what was observed, when, and what was not verified.

  These are conventions, not templates the validator enforces. Only decisions
  and constraints that need their own lifecycle should become separately
  identified objects. Sentences should not become graph nodes.

## 7. Operations

The domain capability comes first. An MCP projection of it is a later and
deliberately narrow step (§9). MCP resources and tools can carry identifiers,
annotations and structured results. They do not carry rules about
applicability, ratification or supersession; those rules live here.

| Operation | Contract |
| --- | --- |
| `knowledge.search` | Text plus filters (`purpose`, `lifecycle`, `repo`, `component`, `subject`). Returns small records: identity, purpose, lifecycle, source revision and digest, applicability, `match.fields` (which fields matched which terms), the best-matching section, and `superseded_by`. |
| `knowledge.get` | Returns one document at an exact revision (`git show <rev>:<path>`) or one bounded section, plus its metadata, source reference and **notices**: superseded / superseded in part / proposed supersession / not ratified / point-in-time / applicability / "ratified is declared, not verified". Output is capped by `max_lines`. |
| `knowledge.resolve_context` | Input: `topic`, `subjects`, `components`, `repos`, `environments`, `versions`, `revisions`, `questions`, `require`, `as_of`. Output: sources sorted into `governing · references · guidance · background · observations · proposals · historical · unclassified`, each with `why`; `excluded` with reasons; `authorities`; `component_states` (`approved` / `intended` / `evidenced` / `transition`); `conflicts`; `unresolved`; `warnings`; and the source manifest. |

`component_states` is the core of the product hypothesis. For `kctl`, run against
the real records above:

```text
approved:   kctl-vuoro-served-knowledge-alignment (ratified 2026-07-21)
intended:   retiring — vuoro-disposition-register, decided 2026-09-17
            (authority delegated by vuoro-agentic-estate)
evidenced:  none → unresolved transition-not-evidenced
proposals:  agentops-s4-evidence-home-preparation, vuoro-kctl-served-hardening-promotion
conflict:   disposition-changed — the July plan is not superseded; it records
            what was approved, not the current destination
```

Unresolved conditions are part of the answer, not errors to suppress:
`no-authority`, `missing-guidance`, `transition-not-evidenced`,
`component-not-in-authority`, `repo-not-in-catalog`, `no-sources`,
`catalog-invalid`.

## 8. Binding sources to work

`resolve --out manifest.json` writes a `vuoro-knowledge-context/v1` manifest:
the request, resolver version, catalog digest and root revisions, and every
source's `doc_id`, role, repo, path, commit, blob, SHA-256 and dirty flag, plus
conflicts, unresolved conditions and warnings. `manifest_digest` covers
everything except `generated_at`, so the same inputs give the same digest.

The manifest attests **which sources were supplied**. It does not attest that
an agent read, understood or followed them. Acceptance evidence still has to
establish the result. A digest does not bring content back, so reconstruction
needs the cited Git objects to stay retrievable. A source that is dirty
(uncommitted) is flagged because Git cannot reconstruct it.

Binding uses the paths that already exist:

* Offline/Git path: `sprintctl event observation add --type work.completed …
  --evidence-ref '{"kind":"artifact","source":"<manifest ref>","revision":"sha256:<bytes digest>"}'`.
  The CLI prints this reference.
* Served path: `--item-id` prints the collector-owned fields of a
  `work.evidence.append-v1` item. These are kind `knowledge-context-manifest`,
  validity `until_inputs_change` with each source's digest in
  `component_digests`, and an `observation` claim. Run binding, chain position
  and idempotency stay with the append owner.

A snapshot does not freeze guidance. At resume and acceptance boundaries,
`recheck <manifest>` resolves the same request again and reports added,
removed, changed or re-roled sources, plus new or cleared conflicts and
unresolved conditions. If any change touches a governing, reference or guidance
source, or a conflict, the status is `review-required` (exit 3) and the work
needs an explicit refresh. A tampered manifest (digest mismatch) is refused.

## 9. Access, editing, and what is deliberately absent

* **Access.** The first increment reads only checkouts the caller passes with
  `--root`, so it exposes nothing the caller cannot already read. A served
  projection must enforce caller permissions *before* returning content,
  snippets or relationship metadata (a relationship can itself be sensitive).
  Document text is never an authorization grant.
* **Editing.** The only write path is a proposed document change through normal
  Git review. The resolver does not write the documents it reads.
* **Not built, on purpose:** a rich-text editor; a connector platform;
  automatic publication of session-derived "knowledge"; a separate approval
  system; a graph database; embeddings. Add embeddings only if the evaluation
  shows a retrieval failure they fix. Not built yet: a served catalog operation
  or MCP tool. Catalog composition is a coordinator-only risk surface in this
  repository and needs its own reviewed change, after the evaluation (§10).

## 10. Evaluation

See `docs/plans/2026-10-05-knowledge-resolution-evaluation.md`. The same corpus
is used for **A** (Git docs with a good index and validated metadata: `search`
plus `get`) and **B** (A plus `resolve_context` and the recorded manifest). The
cases cover intended versus implemented state, supersession in part,
version-specific guidance, cross-repository context, no authoritative answer,
and stale observations. Measured: wrong or obsolete recommendations, operator
corrections, retrieval calls, context volume, and metadata-maintenance effort.
Organizing the source material benefits both arms equally, so A and B always
use the same annotated corpus.

## 11. Findings from running the validator on the estate

Run on 2026-10-05 across vuoro, kctl, agentops, sprintctl, actionq and
vuoro-cloud. These show the contract is useful before Vuoro serves anything:

* 76 documents already carry `doc_id`. None declared `purpose`, so nothing
  could be classified without guessing.
* `status:` uses at least 20 distinct values (`active`, `current`, `final`,
  `implemented`, `ratified-contract`, `partially_superseded`, …). 28 documents
  with `doc_id` have a status that cannot be mapped to a lifecycle without
  judgement.
* Several `supersedes` declarations come from documents whose status cannot be
  mapped (for example `status: active`). Under §5 they do not take effect until
  their lifecycle is declared.
* Six documents are marked superseded with no ratified successor declaring
  `supersedes`. One names a successor that does not name it back.
* Pre-v1 `supersedes` entries often name concepts rather than documents. These
  are reported as `unresolved-legacy-reference` warnings, not errors.
* One document uses the placeholder id `TBD`.
* The estate record cites "disposition-register.yaml (v2, 2026-09-01)", but the
  register is now v3. Delegating by `doc_id` keeps the reference valid across
  versions.

## 12. Decisions needed from the owner

1. Ratify, amend or reject this proposal and the `vuoro-knowledge/v1` field set.
2. Confirm that `lifecycle = "ratified"` correctly describes disposition
   register v3 (decided under delegation, D1). The sidecar declares it; the
   resolver does not verify it.
3. Approve the A/B evaluation before any served/MCP projection is built.
4. Decide whether `knowledge-resolution` needs its own register row or stays
   within `vuoro-core`. It is currently mapped to `vuoro-core`.
