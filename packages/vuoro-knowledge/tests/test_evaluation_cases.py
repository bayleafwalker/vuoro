"""Resolver-level check of the shared evaluation cases, against two naive policies.

This is not the A/B agent study (docs/plans/2026-10-05-knowledge-resolution-evaluation.md).
It shows only that the cases discriminate: the single-ranking policies an agent
falls back on without resolution semantics give the case's ``wrong`` answer,
and the resolver output carries what ``expect`` requires.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vuoro_knowledge.catalog import build_catalog
from vuoro_knowledge.resolve import resolve_context
from vuoro_knowledge.retrieval import search

from knowledge_fixtures import estate, write  # noqa: F401  (pytest fixtures)

CASES = json.loads((Path(__file__).parent / "fixtures/evaluation-cases.json").read_text())


def _catalog(estate):
    return build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]])


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["id"])
def test_resolver_meets_case_expectations(estate, case):
    result = resolve_context(_catalog(estate), {**case["request"], "as_of": CASES["as_of"]})
    expect = case["expect"]
    for key in ("conflicts", "unresolved", "warnings"):
        assert set(expect.get(key, [])) <= {i["code"] for i in result[key]}, key
    for bucket in ("governing", "proposals", "observations"):
        assert set(expect.get(bucket, [])) <= {r["doc_id"] for r in result[bucket]}, bucket
    assert set(expect.get("excluded", [])) <= {e["doc_id"] for e in result["excluded"]}
    for component, status in expect.get("intended", {}).items():
        state = next(s for s in result["component_states"] if s["component"] == component)
        assert state["intended"]["status"] == status
    if "repos" in expect:
        assert set(expect["repos"]) <= {s["repo"] for s in result["sources"]}
    for question, source in expect.get("authorities", {}).items():
        assert {"question": question, "source": source} in [
            {"question": a["question"], "source": a["source"]} for a in result["authorities"]
        ]


def _ratified_first(catalog, query):
    hits = [r for r in search(catalog, query)["results"] if r["lifecycle"] == "ratified"]
    return hits[0]["doc_id"] if hits else None


def _newest_first(catalog, query):
    hits = search(catalog, query)["results"]
    dated = [(r.get("effective") or r.get("ratified_at") or r.get("observed") or "", r["doc_id"]) for r in hits]
    return max(dated)[1] if dated else None


def test_naive_policies_give_the_wrong_answers(estate):
    catalog = _catalog(estate)
    # "Ratified wins" presents the July plan as the whole answer: no destination, no conflict.
    assert _ratified_first(catalog, "kctl knowledge") == "kctl-vuoro-served-knowledge-alignment"
    # "Newest wins" presents a proposal as the answer.
    assert _newest_first(catalog, "kctl") == "agentops-s4-evidence-home-preparation"
    # Neither policy can say that no runbook applies to 0.1.59: search still returns it.
    assert "vuoro-runbook-kctl-publish" in [r["doc_id"] for r in search(catalog, "publishing kctl")["results"]]
