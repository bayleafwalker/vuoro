---
doc_id: vuoro-knowledge-workspace-roadmap
title: Vuoro knowledge workspace roadmap
purpose: proposal
lifecycle: proposed
effective: 2026-10-14
subjects: [knowledge-workspace]
related: [vuoro-knowledge-resolution]
---

# Vuoro Knowledge Workspace Roadmap

> Imported 2026-10-14 from Codex output `vuoro-knowledge-workspace/` (proposal, not ratified).
> It proposes moving document authority from Git into a Vuoro-owned PostgreSQL store for workspace
> knowledge. That conflicts with the boundary in `docs/architecture/knowledge-resolution.md`
> ("does not replace the repositories where that knowledge is authored"); the reconciliation and
> Phase 0 findings are in `docs/plans/2026-10-14-knowledge-workspace-phase0.md`.

Source: [Vuoro Knowledge Workspace Roadmap](https://chatgpt.com/space/page_f3e0bc5d610c81918321606030eadeb8)

## Delivery approach

Draft for planning · 14 October 2026

Deliver a standalone Vuoro workspace that stores versioned artifacts, publishes reviewed knowledge, and lets a later agent reuse both. The first external pilot should complete this workflow without GitHub, Forgejo, or a code repository.

[Product intention](2026-10-14-knowledge-workspace-intention.md) · [Technical design](2026-10-14-knowledge-workspace-design.md)

The sequence below is a planning estimate assuming two engineers with part-time product/design and operational support, and usable authentication and deployment infrastructure. It is not a committed schedule. Phases 0 through 3 total roughly 9–13 weeks if executed sequentially. Reestimate after Phase 0 against the actual codebase and staffing.

| Phase | Estimated duration | Demonstrable outcome |
| --- | --- | --- |
| 0 Contract and discovery | 1 week | Agreed workflow, data contract, and test corpus |
| 1 Storage foundation | 3–4 weeks | Save, version, find, and download work |
| 2 Publication workflow | 2–3 weeks | Propose, review, publish, and reuse with citations |
| 3 External pilot readiness | 3–5 weeks | Reliable operation and measured cross-task reuse |
| 4 Optional integrations | Demand-led | Git and richer retrieval where justified |

Owners below are roles to assign. Completion requires the phase exit evidence, not merely completed implementation tickets.

## Phase 0 Validate the contract

Accountable roles: product lead and engineering lead.

Choose one recurring workflow, such as research producing a report and spreadsheet. Recruit three pilot teams and collect a permission-safe corpus containing both accepted knowledge and superseded work. Define 30 retrieval questions and their expected sources.

Inspect existing Vuoro identity, tenancy, storage, deployment, and agent interfaces. Confirm whether workspace-wide access is sufficient. Agree file types, maximum sizes, workspace quotas, expected corpus and concurrency, retention, hosting region, review policy, and performance envelope.

Produce an API contract, version state machine, publication policy, export manifest schema, and failure scenarios. Assign named owners for product, implementation, and operations.

Exit gate: the team can walk through upload, failed upload, new version, stale proposal, approval, revocation, deletion, and export using agreed examples. Record the initial architecture decision. If Git-native editing is a hard pilot requirement, revisit the backend choice before building the storage layer.

## Phase 1 Store and retrieve

Accountable role: engineering lead. Depends on Phase 0 contracts.

Build workspace membership and roles; artifact identities and immutable versions; checksum verification; upload reservations and finalization; authorized downloads; Markdown revision storage; basic browsing and keyword search; and audit events.

Implement a transactional outbox, idempotent workers, processing-status displays, quotas, and orphan-upload reconciliation. Support Markdown and text-PDF extraction. Store other supported upload formats as originals with explicit extraction status.

Deliver a minimal browser workflow and agent API for create, upload, read, list, and search. Use the same authorization logic for both. Establish database and object backups, basic tombstones, and an export manifest early.

Exit gate: an internal user and an agent can save two versions, retrieve either exact version, and find ready content. Duplicate retries produce one logical result. A missing object, failed extraction, or incomplete upload is visible and recoverable. Automated checks demonstrate workspace isolation and access revocation. No external pilot access yet.

## Phase 2 Review and publish

Accountable roles: product lead and engineering lead. Depends on stable identities, versions, authorization, and the outbox.

Build proposals with expected base revisions; readable changes and source links; reviewer approval/rejection; latest-approved pointers; publication audit history; and version-specific artifact relations.

Add the agent-facing MCP adapter or equivalent integration over the domain API. Return source versions, location anchors, processing status, and indexing freshness. Provide changes-since cursors for incremental agent use.

Default retrieval to approved knowledge and artifacts. Make working-material and historical scopes explicit. Flag knowledge for review when a referenced source changes or becomes unavailable.

Exit gate: an agent produces a spreadsheet and proposes a cited summary; a person approves it; another task retrieves the exact supporting version. Concurrent edits produce a conflict rather than lost work. An agent lacking reviewer authority cannot publish. Drafts do not appear in reader search or counts.

## Phase 3 Pilot and harden

Accountable roles: service owner and product lead. Depends on the complete publication workflow.

Finish retention and physical purge, safe extraction isolation, recovery tooling, cost reporting, and workspace export/import. Add operational dashboards for failures, lag, and quotas. Reconcile worker retries and stale jobs.

Run restore and index-rebuild drills. Verify that deleted records stay deleted after restoration. Test permission changes against reads, search, caches, downloads, relationship traversal, and change feeds. Exercise malformed files, interrupted transfers, object/database mismatches, and worker restarts.

Before opening the external pilot, satisfy the technical release gates below. Then run the three-team pilot, observe repeated use across separate tasks, and evaluate the 30-question retrieval set. Measure time to find reusable work, proposal acceptance, extraction failures, and storage cost.

Exit gate: all technical gates pass, pilot teams complete cross-task reuse, and the agreed retrieval target is met or a documented remediation plan keeps the release restricted. Produce an evidence-backed decision to broaden access, extend the pilot, or revise scope.

## Phase 4 Add integrations

Accountable role: product lead for prioritization and engineering lead for implementation. Start only when a pilot demonstrates a specific need.

| Capability | Trigger | Acceptance evidence |
| --- | --- | --- |
| Git export | A customer needs repository-based review or portability | Approved Markdown exports preserve IDs, versions, and links |
| GitHub ingestion | Useful knowledge already lives in customer repositories | Selected sources ingest with provenance, permission mapping, and revocation behavior |
| Managed Forgejo | Native Git workflows or self-hosting become product requirements | Repository boundaries match access boundaries; operations and identity mapping are validated |
| Richer extraction | Stored slides or spreadsheets are regularly missed in retrieval | Extracted text cites exact slides or sheet/cell locations |
| Semantic retrieval | Keyword evaluation reveals repeatable semantic misses | Improves the fixed evaluation set without weakening access or citation quality |
| Review reminders | Pilot knowledge becomes stale between tasks | Owners receive actionable reminders with controlled frequency |

Implement one authority per content collection. Start integrations with one-way import or export. Bidirectional synchronization, item-specific permissions, live document collaboration, and automatic publication each require a separate design decision and evaluation.

Do not implement both GitHub and Forgejo adapters merely to preserve hypothetical flexibility. Keep domain IDs and APIs independent of providers so the first justified integration can be added cleanly.

## Release gates and sequencing

The critical path is workspace identity and authorization, then versioned storage, then publication, then authorized retrieval and recovery. Git hosting is outside that path unless Phase 0 establishes it as essential.

Before external pilot access, require:

- Workspace isolation and role tests pass across every read and write surface.

- Upload retries, worker retries, and repeated approval requests do not duplicate logical results.

- Stale proposals cannot overwrite a newer approved revision.

- Published content retains exact version references and server-recorded actors.

- Deletion removes content from reads and search; purge and backup-retention behavior are documented and tested.

- A restore drill meets the agreed recovery objectives, and an index can be rebuilt from canonical data.

- Export/import preserves original checksums, IDs within the imported workspace namespace, version relations, and publication history.

- Extraction failures and unsupported types are visible, with no execution of document macros.

- Search and indexing meet the Phase 0 performance envelope and the operating cost is understood.

For broader release, additionally require three teams to complete reuse across two tasks and at least 80 percent top-five source retrieval on the 30-question pilot set. These are proposed thresholds; Phase 0 may refine them before measurement, with the change recorded.

If time is constrained, reduce extraction formats, connector scope, and preview polish first. Preserve authorization, immutable versions, concurrency checks, publication semantics, and recovery. If retrieval quality is weak, inspect extraction and corpus quality before adding a vector index. If granular permissions become essential, revisit the workspace boundary before onboarding affected content.

Next planning action: assign owners, inspect the existing Vuoro implementation, select the first pilot workflow, and convert Phase 0 into an implementation backlog.
