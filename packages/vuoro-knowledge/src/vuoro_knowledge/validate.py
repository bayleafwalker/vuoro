"""Catalog validation: identity, references, lifecycle and contradictions.

The validator checks what a reviewer would otherwise check by hand. It does
not check whether a ``ratified`` claim was actually ratified; that is the
ratification process's gate, and a document asserting it proves nothing.
"""

from __future__ import annotations

from .catalog import Catalog
from .contract import Problem
from .semantics import delegation_effective, is_authoritative, scopes_overlap, supersessions


def validate(catalog: Catalog) -> list[Problem]:
    problems = list(catalog.problems)
    index = supersessions(catalog)

    def add(severity: str, code: str, message: str, doc_id: str | None = None) -> None:
        location = None
        if doc_id and doc_id in catalog.documents:
            doc = catalog.documents[doc_id]
            location = f"{doc.repo}:{doc.path}"
        problems.append(Problem(severity, code, message, doc_id, location))

    for doc in sorted(catalog.documents.values(), key=lambda d: d.doc_id):
        meta = doc.metadata
        if meta.purpose is None:
            add("warning", "purpose-undeclared", "no purpose; the resolver will not classify it", doc.doc_id)
        if meta.lifecycle is None:
            add("warning", "lifecycle-undeclared", "no lifecycle; the resolver treats it as draft", doc.doc_id)
        if meta.lifecycle == "ratified" and not meta.ratified_at:
            add("warning", "ratified-without-date", "ratified without ratified_at", doc.doc_id)
        if meta.purpose == "observation" and not (meta.observed or meta.verified):
            add("error", "observation-undated", "an observation needs observed or verified", doc.doc_id)
        if meta.observed and meta.verified and meta.verified < meta.observed:
            add("error", "contradictory-dates", "verified precedes observed", doc.doc_id)
        if meta.establishes and meta.purpose != "observation":
            add("error", "establishes-not-observation", "only an observation can establish observed state", doc.doc_id)
        if meta.facts is not None and meta.purpose not in ("decision", "specification"):
            add("warning", "facts-not-decision", "status facts on a document that is not a decision", doc.doc_id)
        for subject in meta.subjects:
            if subject not in catalog.subjects:
                add("warning", "unknown-subject", f"subject {subject!r} is not declared in any knowledge.toml", doc.doc_id)
        for relation in meta.relations:
            target = catalog.find(relation.target)
            if target is None:
                # Pre-v1 documents often supersede concepts rather than documents;
                # they are reported, but only a v1 document's broken link is an error.
                add(
                    "error" if meta.v1 else "warning",
                    "broken-reference" if meta.v1 else "unresolved-legacy-reference",
                    f"{relation.kind} target {relation.target!r} is not in the catalog",
                    doc.doc_id,
                )
            elif relation.kind == "supersedes" and not doc.metadata.lifecycle == "ratified":
                add(
                    "warning",
                    "proposed-supersession",
                    f"supersedes {target.doc_id} but its lifecycle is {meta.lifecycle or 'undeclared'}"
                    + (f" (legacy status {meta.legacy_status!r})" if meta.lifecycle is None and meta.legacy_status else "")
                    + "; it takes effect only once ratified",
                    doc.doc_id,
                )
        if meta.governing_decision and catalog.find(meta.governing_decision) is None:
            add("warning", "unresolved-reference", f"governing_decision {meta.governing_decision!r} is outside the catalog", doc.doc_id)
        for successor in meta.superseded_by:
            later = catalog.find(successor)
            if later is None:
                add("warning", "unresolved-reference", f"superseded_by {successor!r} is not in the catalog", doc.doc_id)
            elif not any(s.by == later.doc_id for s in index.get(doc.doc_id, [])):
                add("warning", "one-sided-supersession", f"names {later.doc_id} as successor, but {later.doc_id} does not declare supersedes", doc.doc_id)
        if meta.lifecycle == "superseded" and not any(s.effective for s in index.get(doc.doc_id, [])):
            add("warning", "superseded-without-successor", "lifecycle superseded but no ratified document supersedes it", doc.doc_id)

    # Supersession cycles.
    graph = {
        doc.doc_id: [t.doc_id for r in doc.metadata.relations if r.kind == "supersedes" and (t := catalog.find(r.target))]
        for doc in catalog.documents.values()
    }
    reported: set[frozenset[str]] = set()
    for start in sorted(graph):
        stack = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for nxt in graph.get(node, []):
                if nxt == start:
                    cycle = frozenset(path)
                    if cycle not in reported:
                        reported.add(cycle)
                        add("error", "supersession-cycle", " -> ".join(path + [start]), start)
                elif nxt not in path:
                    stack.append((nxt, path + [nxt]))

    # Delegations: sources must exist; overlapping effective ones must agree.
    effective = []
    for delegation in catalog.delegations:
        source = catalog.find(delegation.source)
        if source is None:
            problems.append(
                Problem("error", "broken-reference", f"delegation source {delegation.source!r} for {delegation.question!r} is not in the catalog", location=delegation.declared_by)
            )
            continue
        if not delegation_effective(catalog, delegation, index):
            add("warning", "delegation-not-authoritative", f"delegates {delegation.question!r} but is not ratified; ignored", delegation.declared_by)
            continue
        if not is_authoritative(catalog, source, index):
            problems.append(
                Problem("warning", "delegated-source-not-authoritative", f"{delegation.question!r} is delegated to {source.doc_id}, which is not ratified or is superseded", source.doc_id, delegation.declared_by)
            )
        effective.append((delegation, source.doc_id))
    for i, (left, left_source) in enumerate(effective):
        for right, right_source in effective[i + 1:]:
            if left.question == right.question and left_source != right_source and scopes_overlap(left.scope, right.scope):
                problems.append(
                    Problem(
                        "error",
                        "conflicting-delegation",
                        f"{left.question!r} delegated to {left_source} by {left.declared_by} and to {right_source} by {right.declared_by}",
                    )
                )
    return problems


def has_errors(problems: list[Problem]) -> bool:
    return any(p.severity == "error" for p in problems)
