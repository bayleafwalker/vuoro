---
doc_id: vuoro-workspace-import-publication
title: Workspace portable import publication invariants
purpose: explanation
lifecycle: draft
effective: 2026-10-17
subjects: [knowledge-workspace]
related: [vuoro-knowledge-workspace-phase0]
---

# Portable import and publication

The workspace owner preserves publication state when importing format-v1 bundles.
An approved revision pointer must name an approved revision in that item; an
approved artifact pointer must name a ready, approved version in that artifact.
A supplied pointer cannot promote a draft or unreviewed object. Markdown revision
bytes must decode as UTF-8 as well as match their checksum. These checks happen
before import starts database or object-store writes.

Approved search checks both the pointer and referenced state. This also excludes
drafts from reader results when a legacy inconsistent pointer is already stored.
Contributor working search retains draft visibility.

The PostgreSQL regressions are in
`packages/vuoro-workspace/tests/test_workspace_hardening.py`; the test context is
`verification/contexts/workspace-import-publication.json`. They establish bounded
preflight refusal and reader projection behavior. They do not establish deployed
migration, concurrent bundle-directory mutation safety, recovery on arbitrary
object stores, or a completed independent-user workflow. Import remains
administrator-only and does not grant effect authority or settle work.
