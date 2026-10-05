"""The motivating case: approved, intended and established are different answers.

July: a ratified plan assigns knowledge semantics to kctl.
September: the status authority marks kctl and knowledge-base retiring.
October: a proposal says the kctl migration is still a proposed follow-on.

Neither "ratified wins" nor "newest wins" answers correctly. The resolver must
keep the July plan as what was approved, report the register's destination,
refuse to call the migration done, and say why.
"""

from __future__ import annotations

from vuoro_knowledge.catalog import build_catalog
from vuoro_knowledge.resolve import recheck, resolve_context
from vuoro_knowledge.validate import validate

from knowledge_fixtures import estate, write  # noqa: F401  (pytest fixtures)


def _resolve(estate, **request):
    catalog = build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]])
    request.setdefault("as_of", "2026-10-05")
    return catalog, resolve_context(catalog, request)


def _ids(records):
    return [r["doc_id"] for r in records]


def test_fixture_catalog_is_valid(estate):
    catalog = build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]])
    assert [p for p in validate(catalog) if p.severity == "error"] == []


def test_approved_intended_and_established_are_reported_separately(estate):
    _, result = _resolve(estate, subjects=["knowledge-resolution"])
    kctl = next(s for s in result["component_states"] if s["component"] == "kctl")

    assert kctl["approved"] == [{"doc_id": "kctl-vuoro-served-knowledge-alignment", "ratified_at": "2026-07-21"}]
    assert kctl["intended"] == {"status": "retiring", "decided": "2026-09-17", "source": "vuoro-disposition-register"}
    assert kctl["evidenced"] == []
    assert kctl["transition"] == {"target": "retired", "established": False}
    assert kctl["proposals"] == ["agentops-s4-evidence-home-preparation"]


def test_the_july_plan_is_not_silently_current_nor_silently_dropped(estate):
    _, result = _resolve(estate, subjects=["knowledge-resolution"])
    assert "kctl-vuoro-served-knowledge-alignment" in _ids(result["governing"])
    conflict = next(c for c in result["conflicts"] if c["code"] == "disposition-changed" and c["component"] == "kctl")
    assert conflict["documents"] == ["kctl-vuoro-served-knowledge-alignment", "vuoro-disposition-register"]
    assert "not superseded" in conflict["message"]


def test_the_october_note_is_a_proposal_not_evidence_of_completion(estate):
    _, result = _resolve(estate, subjects=["knowledge-resolution"])
    assert "agentops-s4-evidence-home-preparation" in _ids(result["proposals"])
    assert "agentops-s4-evidence-home-preparation" not in _ids(result["observations"])
    unresolved = {(u["code"], u.get("component")) for u in result["unresolved"]}
    assert ("transition-not-evidenced", "kctl") in unresolved
    assert ("transition-not-evidenced", "knowledge-base") in unresolved


def test_status_authority_follows_the_delegation(estate):
    _, result = _resolve(estate, subjects=["knowledge-resolution"])
    assert result["authorities"] == [
        {"question": "component-status", "source": "vuoro-disposition-register", "declared_by": ["vuoro-agentic-estate"]}
    ]


def test_the_estate_record_is_a_point_in_time_observation(estate):
    _, result = _resolve(estate, subjects=["knowledge-resolution"], topic="status authority")
    record = next(r for r in result["observations"] if r["doc_id"] == "vuoro-agentic-estate")
    assert record["age_days"] == 23 and record["stale"] is True
    assert any(w["code"] == "stale-observation" for w in result["warnings"])


def test_partial_supersession_keeps_the_document_with_a_warning(estate):
    _, result = _resolve(estate, topic="long-term direction component dispositions")
    record = next(r for r in result["governing"] if r["doc_id"] == "vuoro-long-term-direction")
    assert record["superseded_in_part_by"] == ["vuoro-disposition-register"]
    assert any(w["code"] == "superseded-in-part" for w in result["warnings"])


def test_version_specific_runbook_is_excluded_for_another_generation(estate):
    _, result = _resolve(estate, components=["kctl"], environments=["vuoro-dev"], versions={"vuoro-service": "0.1.59"})
    excluded = {e["doc_id"]: e["reason"] for e in result["excluded"]}
    assert "vuoro-runbook-kctl-publish" in excluded
    assert "0.1.59" in excluded["vuoro-runbook-kctl-publish"]
    assert "vuoro-runbook-kctl-publish" not in _ids(result["guidance"])


def test_version_specific_runbook_applies_to_its_generation(estate):
    _, result = _resolve(estate, components=["kctl"], environments=["vuoro-dev"], versions={"vuoro-service": "0.1.45"})
    assert _ids(result["guidance"]) == ["vuoro-runbook-kctl-publish"]


def test_unchecked_applicability_is_disclosed(estate):
    _, result = _resolve(estate, components=["kctl"])
    record = next(r for r in result["guidance"] if r["doc_id"] == "vuoro-runbook-kctl-publish")
    assert record["unchecked_applicability"] == ["environments", "versions.vuoro-service"]


def test_missing_mandatory_guidance_is_unresolved_not_substituted(estate):
    _, result = _resolve(estate, components=["kctl"], versions={"vuoro-service": "0.1.59"}, require=["runbook"], questions=["interface-contract"])
    codes = {u["code"] for u in result["unresolved"]}
    assert {"missing-guidance", "no-authority"} <= codes


def test_manifest_is_reproducible(estate):
    _, first = _resolve(estate, subjects=["knowledge-resolution"])
    _, second = _resolve(estate, subjects=["knowledge-resolution"])
    assert first["manifest_digest"] == second["manifest_digest"]
    assert first["attests"].startswith("which sources were supplied")
    source = next(s for s in first["sources"] if s["doc_id"] == "kctl-vuoro-served-knowledge-alignment")
    assert source["repo"] == "kctl" and source["role"] == "governing" and len(source["sha256"]) == 64


def test_recheck_requires_review_when_a_governing_source_changes(estate):
    catalog, manifest = _resolve(estate, subjects=["knowledge-resolution"])
    assert recheck(catalog, manifest)["status"] == "unchanged"

    plan = estate["kctl"] / "docs/plans/vuoro-served-knowledge-alignment.md"
    plan.write_text(plan.read_text() + "\nAmended.\n")
    result = recheck(build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]]), manifest)
    assert result["status"] == "review-required"
    assert {"doc_id": "kctl-vuoro-served-knowledge-alignment", "change": "content", "role": "governing"} in result["changes"]


def test_recheck_reports_a_newly_evidenced_transition(estate):
    catalog, manifest = _resolve(estate, subjects=["knowledge-resolution"])
    (estate["vuoro"] / "docs/kctl-retired.md").write_text(
        "---\ndoc_id: vuoro-kctl-retirement-receipt\npurpose: observation\nlifecycle: ratified\n"
        "observed: 2026-10-04\nestablishes:\n  - {component: kctl, state: retired}\n"
        "applies_to:\n  components: [kctl]\n---\n\n# Kctl retirement receipt\n"
    )
    result = recheck(build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]]), manifest)
    cleared = [c for c in result["changes"] if c["change"] == "unresolved-cleared"]
    assert any(c["item"].get("component") == "kctl" for c in cleared)
    kctl = next(s for s in result["current"]["component_states"] if s["component"] == "kctl")
    assert kctl["transition"]["established"] is True
