---
doc_id: vuoro-knowledge-workspace-intention
title: Vuoro knowledge workspace product intention
purpose: proposal
lifecycle: proposed
effective: 2026-10-14
subjects: [knowledge-workspace]
related: [vuoro-knowledge-resolution]
---

# Vuoro Knowledge Workspace Product Intention

> Imported 2026-10-14 from Codex output `vuoro-knowledge-workspace/` (proposal, not ratified).
> It proposes moving document authority from Git into a Vuoro-owned PostgreSQL store for workspace
> knowledge. That conflicts with the boundary in `docs/architecture/knowledge-resolution.md`
> ("does not replace the repositories where that knowledge is authored"); the reconciliation and
> Phase 0 findings are in `docs/plans/2026-10-14-knowledge-workspace-phase0.md`.

Source: [Vuoro Knowledge Workspace Product Intention](https://chatgpt.com/space/page_0b00a8b73c608191bb4c5c668adf44e3)

## Product intention

Draft for product and engineering review · 14 October 2026

Vuoro will provide a persistent knowledge and artifact workspace where people and agents can store work, find reliable context, and improve shared knowledge across tasks. Each workspace exists independently of a code repository and can be created without a GitHub account.

The product should preserve the useful results of agent work: original deliverables, exact versions, supporting evidence, and reviewed conclusions. A later task should be able to reuse that work without reconstructing an earlier conversation.

The initial architecture uses a database for workspace records and document revisions, object storage for artifact bytes, and a derived search index. Git remains an optional integration for curated text. This is a proposed product direction, not a description of deployed Vuoro capabilities.

Related documents: [Design](2026-10-14-knowledge-workspace-design.md) · [Roadmap](2026-10-14-knowledge-workspace-roadmap.md)

## Intended users and outcomes

The initial audience is teams using agents for recurring research, planning, operations, and delivery. The primary workflow spans several tasks and produces both documents and downloadable files.

A contributor saves a spreadsheet, presentation, or report to a workspace. An agent records its sources and proposes a summary. A reviewer publishes the summary as accepted knowledge. Another person or agent retrieves the approved summary and the exact supporting file versions.

The core user outcomes are:

- Preserve useful outputs beyond a chat or agent session.

- Find the latest approved guidance while retaining access to prior versions.

- Distinguish an original source, an agent interpretation, and a reviewed conclusion.

- Update work without silently overwriting another contributor.

- Export knowledge and artifacts in portable formats.

The primary product hypothesis is that dependable reuse across tasks is more valuable than simply accumulating more files. The pilot should test this with repeated real workflows.

## Principles and boundaries

**Separate by default.** Organizational knowledge, research, decisions, and generated deliverables belong in the workspace. Build instructions, API usage, and documentation that must change atomically with code remain in the codebase. Users can link or explicitly import selected repository content.

**Artifacts are first-class records.** A presentation or spreadsheet can exist independently of a summary. Each version has an identity, owner, provenance, and lifecycle.

**Publication is explicit.** Agent-created material begins as a draft or unreviewed artifact. Accepted knowledge requires a publication decision. Recording who approved something does not certify that its claims are true.

**Versions are addressable.** Links and citations can name an exact version. A separate latest-approved link supports everyday navigation.

**Access follows the workspace.** The first release uses workspace-wide membership and roles. Material requiring a different audience belongs in a different workspace. Cross-workspace references never grant access.

**Portability is part of the product.** Exports include original files, Markdown, metadata, versions, and relationships. Search indexes can be rebuilt.

The initial scope excludes a code forge UI, an office suite, real-time multiplayer editing, arbitrary document-level sharing, autonomous publication, and general-purpose agent scratch memory. External repository synchronization and semantic search follow demonstrated demand.

## Success criteria

Proposed pilot acceptance targets, to be calibrated during discovery:

- At least three pilot teams complete the save, review, retrieve, and reuse workflow across two separate tasks.

- At least 80 percent of a curated set of 30 representative retrieval questions return the correct accessible source in the top five results.

- Every published knowledge revision identifies an author or producing agent, an approver, and its supporting source versions or an explicit statement that no external sources were supplied.

- Every artifact version can be downloaded with a matching checksum; retries do not create duplicate versions.

- Access isolation, stale-write detection, deletion exclusion, and restore checks pass before external pilot access.

- Teams can export and reimport a workspace without losing original bytes, stable record IDs, version relationships, or publication history.

Measure time to find reusable work, weekly reuse across tasks, proposal acceptance, stale knowledge, extraction failures, and storage cost per active workspace. Set improvement targets after collecting the pilot baseline.
