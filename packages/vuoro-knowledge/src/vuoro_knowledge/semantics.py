"""Shared interpretation rules: which declarations take effect, and why.

Two rules carry most of the weight:

* A declaration takes effect only when the declaring document is itself
  authoritative (``lifecycle: ratified`` and not fully superseded), or when it
  sits in the repository's reviewed ``knowledge.toml``. A proposal that says it
  supersedes something does not supersede it; it proposes to.
* No universal ranking. Newer does not beat older and ``decision`` does not
  beat ``observation``. Precedence is whatever a delegation says for a named
  question; where none is declared, or two collide, the result is a conflict.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Catalog, Document
from .contract import Delegation

TRANSITIONAL_STATUSES = frozenset({"retiring", "promote", "hold", "deferred", "open", "unknown"})
# For a transitional component status, the observed state that would show the
# transition was actually carried out.
TRANSITION_TARGET = {"retiring": "retired", "promote": "promoted", "deferred": "active"}


@dataclass(frozen=True)
class Supersession:
    target: str
    by: str
    in_part: bool
    scope: str | None
    effective: bool  # False when declared by a non-authoritative document


def is_ratified(doc: Document) -> bool:
    return doc.metadata.lifecycle == "ratified"


def supersessions(catalog: Catalog) -> dict[str, list[Supersession]]:
    out: dict[str, list[Supersession]] = {}
    for doc in catalog.documents.values():
        for relation in doc.metadata.relations:
            if relation.kind != "supersedes":
                continue
            target, fragment = catalog.locate(relation.target)
            if target is None:
                continue
            # ``doc_id:R1`` supersedes one part of a document, never all of it.
            in_part = relation.in_part or fragment is not None
            out.setdefault(target.doc_id, []).append(
                Supersession(target.doc_id, doc.doc_id, in_part, relation.scope or fragment, is_ratified(doc))
            )
    return out


def fully_superseded_by(catalog: Catalog, doc: Document, index: dict[str, list[Supersession]]) -> list[str]:
    return sorted(s.by for s in index.get(doc.doc_id, []) if s.effective and not s.in_part)


def is_authoritative(catalog: Catalog, doc: Document, index: dict[str, list[Supersession]]) -> bool:
    return is_ratified(doc) and not fully_superseded_by(catalog, doc, index)


def delegation_effective(catalog: Catalog, delegation: Delegation, index: dict[str, list[Supersession]]) -> bool:
    if delegation.declared_by.endswith(":knowledge.toml"):
        return True
    declarer = catalog.documents.get(delegation.declared_by)
    return declarer is not None and is_authoritative(catalog, declarer, index)


def scope_matches(scope: dict[str, tuple[str, ...]], context: dict[str, list[str]]) -> bool:
    """A scope dimension constrains only when declared; ``*`` matches anything."""

    for dimension, values in scope.items():
        if not values or "*" in values:
            continue
        wanted = context.get(dimension) or []
        if wanted and not set(values) & set(wanted):
            return False
    return True


def scopes_overlap(a: dict[str, tuple[str, ...]], b: dict[str, tuple[str, ...]]) -> bool:
    for dimension in set(a) & set(b):
        left, right = set(a[dimension]), set(b[dimension])
        if "*" in left or "*" in right or not left or not right:
            continue
        if not left & right:
            return False
    return True


def effective_delegations(catalog: Catalog, question: str, context: dict[str, list[str]]) -> list[Delegation]:
    index = supersessions(catalog)
    return [
        d
        for d in catalog.delegations
        if d.question == question and delegation_effective(catalog, d, index) and scope_matches(d.scope, context)
    ]
