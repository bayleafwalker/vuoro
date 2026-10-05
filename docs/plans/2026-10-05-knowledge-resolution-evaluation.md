---
doc_id: vuoro-knowledge-resolution-evaluation
title: Knowledge resolution A/B evaluation
purpose: proposal
lifecycle: proposed
effective: 2026-10-05
applies_to:
  components: [vuoro-core]
subjects: [knowledge-resolution]
supported_by: [vuoro-knowledge-resolution]
---

# Knowledge resolution: A/B evaluation protocol

Status: **proposed**. This protocol tests whether `resolve_context` and
source binding reduce wrong answers compared with a strong Git baseline. It
should be run before any served or MCP projection is built.

## Arms

| Arm | The agent gets |
| --- | --- |
| **A** | The annotated corpus in Git with validated `vuoro-knowledge/v1` metadata, plus `vuoro-knowledge search` and `get` (a good index with metadata filters and section reads). |
| **B** | Everything in A, plus `vuoro-knowledge resolve` and the recorded manifest. |

Both arms use the same model, harness, prompt template, corpus revision and
budget. Arm A is a strong baseline, not a weak one. Organizing the documents
improves both arms equally, so any credit to B has to come from the resolution
semantics.

## Cases

The cases are in `packages/vuoro-knowledge/tests/fixtures/evaluation-cases.json`.
Each case has a question, a work-context request, what a correct answer must
contain (`expect`), and the plausible wrong answer it is designed to catch
(`wrong`):

1. **intended-vs-implemented**: the Kctl July plan, September register and October note.
2. **follow-supersession-in-part**: the long-term direction, superseded only in its dispositions.
3. **version-specific-guidance**: a runbook for vuoro-service 0.1.40–0.1.49 asked about 0.1.59.
4. **cross-repository-context**: sources spread across vuoro, kctl and agentops.
5. **no-authoritative-answer**: a required question with no delegation.
6. **point-in-time-observation**: a ratified observation dated 2026-09-12, asked about on 2026-10-05.

`test_evaluation_cases.py` checks the resolver side of every case. It also shows
that "ratified wins" and "newest wins" give the `wrong` answers. That only proves
the cases discriminate. It is not the agent study.

The study needs a second corpus: the real annotated estate at a pinned revision
of each repository (vuoro `knowledge.toml`, the Kctl alignment plan, the
agentops 2026-10-03 note). This guards against overfitting to the fixture.

## Measures

For each case and arm, over at least five runs:

* **Wrong or obsolete recommendation**: the answer contains the case's `wrong`
  claim, or omits an `expect` item. Graded blind against `expect`.
* **Unresolved handled visibly**: when mandatory guidance is missing or in
  conflict, the answer says so. Substituting a plausible answer counts as
  wrong.
* **Operator corrections** needed before the answer is acceptable.
* **Retrieval calls** and **context volume** (tokens of retrieved content).
* **Metadata-maintenance effort**: minutes and lines of metadata needed to
  annotate the corpus, recorded once and kept separately from per-run results.

## Decision rule

Proceed to a served operation only if B reduces wrong or obsolete
recommendations on cases 1, 3 and 5 without increasing operator corrections,
and the maintenance cost stays within what the owner accepts. If A and B are
equal, the value was in the metadata and structure. In that case keep the
contract and the validator, and stop at the CLI.
