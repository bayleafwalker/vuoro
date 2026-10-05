"""``knowledge.resolve_context``: the applicable source set for a piece of work.

The result answers three different questions separately and never merges
them into one ``current`` flag:

* what was approved (ratified, authoritative decisions);
* what the intended destination is (the status a delegated authority records);
* what has actually been established (observations that say so, with dates).

Where these disagree, or where a mandatory answer has no authority, the result
says so as a conflict or an unresolved condition. It never chooses for the
reader.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from . import __version__
from .catalog import Catalog, Document, canonical, sha256_hex
from .contract import satisfies
from .retrieval import match_document, summary, tokens
from .semantics import (
    TRANSITION_TARGET,
    TRANSITIONAL_STATUSES,
    effective_delegations,
    fully_superseded_by,
    is_authoritative,
    supersessions,
)
from .validate import validate

MANIFEST_SCHEMA = "vuoro-knowledge-context/v1"
RESOLVER = {"name": "vuoro-knowledge.resolve_context", "version": __version__}
BUCKETS = (
    "governing",
    "references",
    "guidance",
    "background",
    "observations",
    "proposals",
    "historical",
    "unclassified",
)
TEXT_THRESHOLD = 2.0
DEFAULT_OBSERVATION_AGE_DAYS = 14
STATUS_QUESTION = "component-status"

REQUEST_KEYS = (
    "topic",
    "subjects",
    "components",
    "repos",
    "environments",
    "versions",
    "revisions",
    "questions",
    "require",
    "as_of",
    "max_observation_age_days",
)


def normalize_request(request: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(request) - set(REQUEST_KEYS))
    if unknown:
        raise ValueError(f"unknown request keys: {unknown}")
    out: dict[str, Any] = {"topic": str(request.get("topic") or "")}
    for key in ("subjects", "components", "repos", "environments", "questions", "require"):
        values = request.get(key) or []
        out[key] = sorted({values} if isinstance(values, str) else {str(v) for v in values})
    out["versions"] = {str(k): str(v) for k, v in sorted((request.get("versions") or {}).items())}
    out["revisions"] = {str(k): str(v) for k, v in sorted((request.get("revisions") or {}).items())}
    as_of = request.get("as_of") or _dt.date.today().isoformat()
    _dt.date.fromisoformat(str(as_of))
    out["as_of"] = str(as_of)
    out["max_observation_age_days"] = int(request.get("max_observation_age_days") or DEFAULT_OBSERVATION_AGE_DAYS)
    return out


def _date(value: str | None) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _applicability(doc: Document, request: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Return (exclusion reason, unchecked dimensions)."""

    applies = doc.metadata.applies_to
    unchecked = []
    for dimension in ("repos", "components", "environments"):
        declared = applies.get(dimension)
        if not declared or "*" in declared:
            continue
        wanted = request[dimension]
        if dimension == "components":
            wanted = request["_components"]
        if not wanted:
            if dimension == "environments":
                unchecked.append(dimension)
            continue
        if dimension != "components" and not set(declared) & set(wanted):
            return f"applies to {dimension} {declared}, work targets {wanted}", unchecked
    for component, spec in (applies.get("versions") or {}).items():
        version = request["versions"].get(component)
        if version is None:
            unchecked.append(f"versions.{component}")
        elif not satisfies(version, spec):
            return f"applies to {component} {spec}, work targets {version}", unchecked
    return None, unchecked


def _bucket(catalog: Catalog, doc: Document, index) -> str:
    meta = doc.metadata
    if fully_superseded_by(catalog, doc, index) or meta.lifecycle in ("superseded", "withdrawn"):
        return "historical"
    if meta.purpose is None:
        return "unclassified"
    if meta.purpose == "observation":
        return "observations"
    if meta.purpose == "proposal" or meta.lifecycle in (None, "draft", "proposed"):
        return "proposals"
    if meta.purpose == "explanation":
        return "background"
    return {"decision": "governing", "specification": "references", "runbook": "guidance"}[meta.purpose]


def _issue(code: str, message: str, **detail: Any) -> dict[str, Any]:
    out = {"code": code, "message": message}
    out.update({k: v for k, v in detail.items() if v not in (None, [], {})})
    return out


def resolve_context(catalog: Catalog, request: dict[str, Any]) -> dict[str, Any]:
    req = normalize_request(request)
    as_of = _dt.date.fromisoformat(req["as_of"])
    index = supersessions(catalog)
    conflicts: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    errors = [p for p in validate(catalog) if p.severity == "error"]
    if errors:
        unresolved.append(
            _issue("catalog-invalid", f"{len(errors)} catalog validation error(s); results may omit or misclassify sources", problems=[p.to_dict() for p in errors[:20]])
        )

    for subject in req["subjects"]:
        if subject not in catalog.subjects:
            warnings.append(_issue("unknown-subject", f"subject {subject!r} is not declared"))
    components = set(req["components"])
    for subject in req["subjects"]:
        if subject in catalog.subjects:
            components.update(catalog.subjects[subject].components)
    req["_components"] = sorted(components)
    scope_context = {"components": req["_components"], "repos": req["repos"], "environments": req["environments"]}

    roots = {r["repo"]: r for r in catalog.roots}
    for repo, revision in req["revisions"].items():
        root = roots.get(repo)
        if root is None:
            unresolved.append(_issue("repo-not-in-catalog", f"work targets {repo}@{revision[:12]} but the catalog has no checkout of it", repo=repo))
        elif root["revision"] and not root["revision"].startswith(revision) and not revision.startswith(root["revision"]):
            warnings.append(
                _issue("revision-mismatch", f"catalog read {repo}@{(root['revision'] or '')[:12]}, work targets {revision[:12]}; guidance may describe another revision", repo=repo)
            )

    # 1. Relevance and applicability.
    terms = tokens(req["topic"])
    selected: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, Any]] = []

    def consider(doc: Document, why: list[str]) -> None:
        reason, unchecked = _applicability(doc, req)
        if reason:
            excluded.append({"doc_id": doc.doc_id, "reason": reason, "why_considered": why})
            return
        entry = selected.setdefault(doc.doc_id, {"why": []})
        entry["why"] = sorted(set(entry["why"]) | set(why))
        if unchecked:
            entry["unchecked"] = sorted(set(entry.get("unchecked", [])) | set(unchecked))

    for doc in sorted(catalog.documents.values(), key=lambda d: d.doc_id):
        why = []
        doc_components = set(doc.metadata.applies_to.get("components", []))
        if doc_components & components:
            why.append("component: " + ", ".join(sorted(doc_components & components)))
        if set(doc.metadata.subjects) & set(req["subjects"]):
            why.append("subject: " + ", ".join(sorted(set(doc.metadata.subjects) & set(req["subjects"]))))
        score, explanation, _section = match_document(doc, terms)
        if score >= TEXT_THRESHOLD:
            matched = sorted({t for ts in explanation.values() for t in ts})
            why.append("topic: " + ", ".join(matched))
        if why:
            consider(doc, why)

    # 2. Question-scoped authorities.
    authorities: list[dict[str, Any]] = []
    questions = list(req["questions"])
    if components and STATUS_QUESTION not in questions:
        questions.append(STATUS_QUESTION)
    status_sources: dict[str, Document] = {}
    for question in questions:
        delegations = effective_delegations(catalog, question, scope_context)
        sources = sorted({d.source for d in delegations})
        resolved = [catalog.find(s) for s in sources]
        if not delegations:
            if question in req["questions"]:
                unresolved.append(_issue("no-authority", f"no ratified delegation names a source for {question!r} in this scope", question=question))
            continue
        if len({d.doc_id for d in resolved if d}) > 1:
            conflicts.append(
                _issue(
                    "authority-conflict",
                    f"{question!r} is delegated to more than one source in this scope; not choosing",
                    question=question,
                    sources=sources,
                    declared_by=sorted({d.declared_by for d in delegations}),
                )
            )
            continue
        source = resolved[0]
        if source is None:
            unresolved.append(_issue("no-authority", f"{question!r} is delegated to {sources[0]!r}, which is not in the catalog", question=question))
            continue
        authorities.append({"question": question, "source": source.doc_id, "declared_by": sorted({d.declared_by for d in delegations})})
        consider(source, [f"authority: {question}"])
        for delegation in delegations:
            declarer = catalog.documents.get(delegation.declared_by)
            if declarer is not None:
                consider(declarer, [f"delegates: {question}"])
        if question == STATUS_QUESTION:
            for component in components:
                status_sources[component] = source

    # 3. Classify.
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in BUCKETS}
    placed: dict[str, str] = {}
    for doc_id, entry in sorted(selected.items()):
        doc = catalog.documents[doc_id]
        bucket = _bucket(catalog, doc, index)
        record = summary(doc)
        record["why"] = entry["why"]
        if entry.get("unchecked"):
            record["unchecked_applicability"] = entry["unchecked"]
            warnings.append(
                _issue("applicability-unchecked", f"{doc_id} is constrained by {entry['unchecked']} but the work context does not say", doc_id=doc_id)
            )
        superseded = fully_superseded_by(catalog, doc, index)
        if superseded:
            record["superseded_by"] = superseded
        partial = [s.by for s in index.get(doc_id, []) if s.effective and s.in_part]
        if partial:
            record["superseded_in_part_by"] = sorted(partial)
            warnings.append(_issue("superseded-in-part", f"{doc_id} is superseded in part by {sorted(partial)}", doc_id=doc_id))
        proposed = [s.by for s in index.get(doc_id, []) if not s.effective]
        if proposed:
            record["proposed_supersession_by"] = sorted(proposed)
        if bucket == "observations":
            when = _date(doc.metadata.verified or doc.metadata.observed)
            if when is not None:
                age = (as_of - when).days
                record["age_days"] = age
                record["stale"] = age > req["max_observation_age_days"]
                if record["stale"]:
                    warnings.append(
                        _issue("stale-observation", f"{doc_id} was observed {age} days before {req['as_of']}; treat it as history, not current state", doc_id=doc_id)
                    )
        if doc.dirty:
            warnings.append(_issue("uncommitted-source", f"{doc_id} differs from its committed revision; the manifest cannot be reconstructed from Git", doc_id=doc_id))
        buckets[bucket].append(record)
        placed[doc_id] = bucket

    # 4. Component states: approved / intended / evidenced, kept apart.
    component_states = []
    for component in sorted(components):
        approved = [
            d
            for d in catalog.documents.values()
            if placed.get(d.doc_id) == "governing" and component in d.metadata.applies_to.get("components", [])
        ]
        evidenced = sorted(
            (
                {"doc_id": d.doc_id, "state": e.state, "observed": d.metadata.verified or d.metadata.observed}
                for d in catalog.documents.values()
                if d.doc_id in placed and d.metadata.purpose == "observation"
                for e in d.metadata.establishes
                if e.component == component
            ),
            key=lambda e: (e["observed"] or "", e["doc_id"]),
        )
        state: dict[str, Any] = {
            "component": component,
            "approved": [{"doc_id": d.doc_id, "ratified_at": d.metadata.ratified_at or d.metadata.effective} for d in sorted(approved, key=lambda d: d.doc_id)],
            "intended": None,
            "evidenced": evidenced,
            "proposals": sorted(
                d.doc_id
                for d in catalog.documents.values()
                if placed.get(d.doc_id) == "proposals" and component in d.metadata.applies_to.get("components", [])
            ),
        }
        source = status_sources.get(component)
        if source is not None:
            fact = source.facts.get(component)
            if fact is None:
                unresolved.append(
                    _issue("component-not-in-authority", f"{source.doc_id} is the status authority but records no status for {component!r}", component=component)
                )
            else:
                state["intended"] = {"status": fact["status"], "decided": fact["decided"], "source": source.doc_id}
                decided = _date(fact["decided"])
                if fact["status"] in TRANSITIONAL_STATUSES:
                    target = TRANSITION_TARGET.get(fact["status"])
                    done = [
                        e for e in evidenced
                        if target and e["state"] == target and (decided is None or (_date(e["observed"]) or _dt.date.min) >= decided)
                    ]
                    state["transition"] = {"target": target, "established": bool(done)}
                    if not done:
                        unresolved.append(
                            _issue(
                                "transition-not-evidenced",
                                f"{component} is {fact['status']} per {source.doc_id}"
                                + (f" (decided {fact['decided']})" if fact["decided"] else "")
                                + (f"; no observation establishes it is {target}" if target else "; the destination is not decided"),
                                component=component,
                                proposals=state["proposals"],
                            )
                        )
                if fact["status"] in ("retiring", "retired"):
                    for doc in approved:
                        approved_on = _date(doc.metadata.ratified_at or doc.metadata.effective)
                        if decided is None or approved_on is None or approved_on < decided:
                            conflicts.append(
                                _issue(
                                    "disposition-changed",
                                    f"{doc.doc_id} (ratified {doc.metadata.ratified_at or 'undated'}) governs {component}, "
                                    f"but {source.doc_id} later marks {component} {fact['status']}"
                                    + (f" ({fact['decided']})" if fact["decided"] else "")
                                    + ". It is not superseded: it records what was approved, not the current destination.",
                                    component=component,
                                    documents=[doc.doc_id, source.doc_id],
                                )
                            )
        component_states.append(state)

    # 5. Required guidance.
    for purpose in req["require"]:
        bucket = {"decision": "governing", "specification": "references", "runbook": "guidance", "explanation": "background", "observation": "observations"}.get(purpose)
        if bucket is None or not buckets[bucket]:
            unresolved.append(_issue("missing-guidance", f"no applicable ratified {purpose} for this work context", purpose=purpose))

    if not selected:
        unresolved.append(_issue("no-sources", "no catalogued source matched the work context"))

    sources = []
    for bucket in BUCKETS:
        for record in buckets[bucket]:
            doc = catalog.documents[record["doc_id"]]
            ref = doc.source_ref()
            sources.append({"doc_id": doc.doc_id, "role": bucket, **ref})

    request_out = {k: v for k, v in req.items() if not k.startswith("_")}
    body = {
        "schema": MANIFEST_SCHEMA,
        "resolver": RESOLVER,
        "request": request_out,
        "expanded_components": req["_components"],
        "catalog": {"digest": catalog.digest(), "roots": catalog.roots},
        "authorities": authorities,
        "component_states": component_states,
        **buckets,
        "excluded": sorted(excluded, key=lambda e: e["doc_id"]),
        "conflicts": conflicts,
        "unresolved": unresolved,
        "warnings": warnings,
        "sources": sorted(sources, key=lambda s: s["doc_id"]),
        "attests": "which sources were supplied for this work context; not that they were read, understood or followed",
    }
    body["manifest_digest"] = manifest_digest(body)
    return body


def manifest_digest(manifest: dict[str, Any]) -> str:
    body = {k: v for k, v in manifest.items() if k not in ("manifest_digest", "generated_at")}
    return "sha256:" + sha256_hex(canonical(body))


def recheck(catalog: Catalog, manifest: dict[str, Any]) -> dict[str, Any]:
    """Compare a recorded manifest with what the same request resolves to now.

    Used at resume and acceptance boundaries: a context snapshot does not
    freeze guidance; a governing change requires an explicit refresh.
    """

    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"not a {MANIFEST_SCHEMA} manifest")
    if manifest_digest(manifest) != manifest.get("manifest_digest"):
        raise ValueError("manifest_digest does not match the manifest body")
    current = resolve_context(catalog, manifest["request"])
    before = {s["doc_id"]: s for s in manifest["sources"]}
    after = {s["doc_id"]: s for s in current["sources"]}
    changes = []
    for doc_id in sorted(set(before) | set(after)):
        old, new = before.get(doc_id), after.get(doc_id)
        if old is None:
            changes.append({"doc_id": doc_id, "change": "added", "role": new["role"]})
        elif new is None:
            changes.append({"doc_id": doc_id, "change": "removed", "role": old["role"]})
        else:
            if old["sha256"] != new["sha256"]:
                changes.append({"doc_id": doc_id, "change": "content", "role": new["role"]})
            if old["role"] != new["role"]:
                changes.append({"doc_id": doc_id, "change": "role", "from": old["role"], "to": new["role"]})

    def codes(items: list[dict[str, Any]]) -> set[str]:
        return {canonical({k: v for k, v in i.items() if k != "problems"}) for i in items}

    for key in ("conflicts", "unresolved"):
        added = codes(current[key]) - codes(manifest[key])
        cleared = codes(manifest[key]) - codes(current[key])
        for item in sorted(added):
            changes.append({"change": f"{key}-added", "item": json.loads(item)})
        for item in sorted(cleared):
            changes.append({"change": f"{key}-cleared", "item": json.loads(item)})
    governing_roles = {"governing", "references", "guidance"}
    requires_review = any(
        c.get("role") in governing_roles or c.get("from") in governing_roles or c.get("to") in governing_roles or c["change"].startswith("conflicts")
        for c in changes
    )
    return {
        "status": "unchanged" if not changes else ("review-required" if requires_review else "changed"),
        "recorded_digest": manifest["manifest_digest"],
        "current_digest": current["manifest_digest"],
        "changes": changes,
        "current": current,
    }
