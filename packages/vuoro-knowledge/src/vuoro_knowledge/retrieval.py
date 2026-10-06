"""``knowledge.search`` and ``knowledge.get``: bounded discovery and exact reads."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .catalog import Catalog, Document, git_show, parse_sections, sha256_hex, split_frontmatter
from .semantics import fully_superseded_by, supersessions

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]+")
DEFAULT_LIMIT = 10
DEFAULT_MAX_LINES = 400


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _components(doc: Document) -> list[str]:
    return list(doc.metadata.applies_to.get("components", []))


def match_document(doc: Document, terms: list[str]) -> tuple[float, dict[str, list[str]], str | None]:
    """Score a document and explain which fields matched which terms."""

    if not terms:
        return 0.0, {}, None
    fields = {
        "doc_id": (set(tokens(doc.doc_id.replace(".", " "))), 5.0),
        "title": (set(tokens(doc.title)), 5.0),
        "subjects": (set(t for s in doc.metadata.subjects for t in tokens(s)), 4.0),
        "components": (set(t for c in _components(doc) for t in tokens(c)), 4.0),
        "headings": (set(t for s in doc.sections for t in tokens(s.heading)), 3.0),
    }
    body_tokens = tokens(doc.text)
    body_counts: dict[str, int] = {}
    for token in body_tokens:
        body_counts[token] = body_counts.get(token, 0) + 1
    score = 0.0
    explanation: dict[str, list[str]] = {}
    for term in terms:
        for name, (vocab, weight) in fields.items():
            if term in vocab:
                score += weight
                explanation.setdefault(name, []).append(term)
        if body_counts.get(term):
            score += min(body_counts[term], 5) * 0.5
            explanation.setdefault("body", []).append(term)
    if score and len({t for ts in explanation.values() for t in ts}) < len(set(terms)):
        # Partial matches rank below documents matching every term.
        score *= 0.5
    best = None
    best_hits = 0
    lines = doc.text.splitlines()
    for section in doc.sections:
        chunk = " ".join(lines[section.start_line - 1 : section.end_line]).lower()
        hits = sum(chunk.count(term) for term in terms)
        if hits > best_hits:
            best, best_hits = section.slug, hits
    return score, explanation, best


def _filters_match(doc: Document, filters: dict[str, Any]) -> bool:
    meta = doc.metadata
    checks = {
        "purpose": lambda v: meta.purpose in v,
        "lifecycle": lambda v: meta.lifecycle in v,
        "repo": lambda v: doc.repo in v,
        "component": lambda v: bool(set(_components(doc)) & set(v)),
        "subject": lambda v: bool(set(meta.subjects) & set(v)),
    }
    for key, value in filters.items():
        if value in (None, [], ()):
            continue
        values = [value] if isinstance(value, str) else list(value)
        if key in checks and not checks[key](values):
            return False
    return True


def summary(doc: Document) -> dict[str, Any]:
    meta = doc.metadata
    out = {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "purpose": meta.purpose,
        "lifecycle": meta.lifecycle,
        "source": doc.source_ref(),
    }
    if meta.applies_to:
        out["applies_to"] = meta.applies_to
    for key in ("effective", "observed", "verified", "ratified_at"):
        if getattr(meta, key):
            out[key] = getattr(meta, key)
    return out


def search(catalog: Catalog, query: str, *, filters: dict[str, Any] | None = None, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    terms = tokens(query)
    filters = filters or {}
    index = supersessions(catalog)
    results = []
    for doc in catalog.documents.values():
        if not _filters_match(doc, filters):
            continue
        score, explanation, section = match_document(doc, terms)
        if terms and score <= 0:
            continue
        result = summary(doc)
        result["score"] = round(score, 2)
        result["match"] = {"fields": explanation}
        if section:
            result["match"]["section"] = section
        superseded = fully_superseded_by(catalog, doc, index)
        if superseded:
            result["superseded_by"] = superseded
        results.append(result)
    results.sort(key=lambda r: (-r["score"], r["doc_id"]))
    return {
        "query": query,
        "filters": {k: v for k, v in filters.items() if v not in (None, [], ())},
        "catalog_digest": catalog.digest(),
        "total": len(results),
        "results": results[:limit],
    }


def notices(catalog: Catalog, doc: Document) -> list[dict[str, Any]]:
    """Facts a reader must not lose when reading only one section."""

    index = supersessions(catalog)
    out = []
    meta = doc.metadata
    superseded = fully_superseded_by(catalog, doc, index)
    if superseded:
        out.append({"code": "superseded", "message": f"superseded by {', '.join(superseded)}"})
    for s in index.get(doc.doc_id, []):
        if s.effective and s.in_part:
            out.append({"code": "superseded-in-part", "message": f"superseded in part by {s.by}" + (f" ({s.scope})" if s.scope else "")})
        if not s.effective:
            out.append({"code": "proposed-supersession", "message": f"{s.by} proposes to supersede this document; not in effect"})
    if meta.purpose == "proposal" or meta.lifecycle in ("draft", "proposed"):
        out.append({"code": "not-ratified", "message": f"{meta.purpose or 'document'} with lifecycle {meta.lifecycle or 'undeclared'}; not operating guidance"})
    if meta.purpose == "observation":
        when = meta.verified or meta.observed
        out.append({"code": "point-in-time", "message": f"observation as of {when}; not a statement about later state"})
    if meta.applies_to:
        out.append({"code": "applicability", "message": "applies only to " + "; ".join(f"{k}={v}" for k, v in sorted(meta.applies_to.items()))})
    if meta.lifecycle == "ratified":
        out.append({"code": "declared-lifecycle", "message": "ratified is the document's own declaration; it is not verified here"})
    return out


def get(
    catalog: Catalog,
    doc_id: str,
    *,
    section: str | None = None,
    revision: str | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
) -> dict[str, Any]:
    doc = catalog.find(doc_id)
    if doc is None:
        raise KeyError(f"unknown document {doc_id!r}")
    text = doc.text
    source = doc.source_ref()
    sections = doc.sections
    if revision is not None and revision != doc.revision:
        retrieved = git_show(Path(doc.root), revision, doc.path)
        if retrieved is None:
            raise KeyError(f"{doc.repo}:{doc.path} is not retrievable at revision {revision!r}")
        text = retrieved
        source = {
            "repo": doc.repo,
            "path": doc.path,
            "revision": revision,
            "blob": None,
            "sha256": sha256_hex(retrieved.encode()),
            "dirty": False,
        }
        _title, sections = parse_sections(text)
    lines = text.splitlines()
    start, end = 1, len(lines)
    if section is not None:
        match = next((s for s in sections if s.slug == section), None)
        if match is None:
            raise KeyError(f"{doc.doc_id} has no section {section!r}; sections: {[s.slug for s in sections]}")
        start, end = match.start_line, match.end_line
    else:
        _front, _body, offset = split_frontmatter(text)
        start = offset + 1
    truncated = end - start + 1 > max_lines
    if truncated:
        end = start + max_lines - 1
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "metadata": doc.metadata.to_dict(),
        "source": source,
        "section": section,
        "lines": {"start": start, "end": end, "total": len(lines)},
        "truncated": truncated,
        "notices": notices(catalog, doc),
        "sections": [{"slug": s.slug, "heading": s.heading} for s in sections],
        "content": "\n".join(lines[start - 1 : end]),
    }
