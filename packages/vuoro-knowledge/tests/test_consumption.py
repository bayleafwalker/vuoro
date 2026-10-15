"""Session obligations are independent of ranking, authority and access."""
from pathlib import Path

import pytest

from vuoro_knowledge.catalog import build_catalog
from vuoro_knowledge.resolve import recheck, resolve_context
from vuoro_knowledge.retrieval import search


def document(root: Path, doc_id="migration-guide", *, mode="required-on-match", purpose="explanation", lifecycle="ratified", scope="operations: [migration]"):
    path = root / f"{doc_id}.md"
    path.write_text(f"---\ndoc_id: {doc_id}\npurpose: {purpose}\nlifecycle: {lifecycle}\nconsumption: {mode}\napplies_to:\n  {scope}\n---\n# Context\n\nPrerequisite details.\n")
    return path


def test_required_context_does_not_depend_on_search_ranking(tmp_path):
    document(tmp_path)
    result = resolve_context(build_catalog([tmp_path]), {"operations": ["migration"], "topic": "unrelated"})
    assert result["consumption"] == {"status": "ready", "required": [{"doc_id": "migration-guide", "status": "ready", "reasons": []}]}
    assert result["background"][0]["why"] == ["consumption: required-on-match"]
    assert result["authorities"] == []  # consumption cannot appoint an authority
    assert resolve_context(build_catalog([tmp_path]), {"operations": ["release"]})["sources"] == []


@pytest.mark.parametrize("lifecycle,purpose", [("proposed", "runbook"), ("ratified", "proposal"), ("superseded", "runbook"), ("draft", "observation")])
def test_required_proposal_or_history_cannot_become_procedure(tmp_path, lifecycle, purpose):
    document(tmp_path, lifecycle=lifecycle, purpose=purpose)
    result = resolve_context(build_catalog([tmp_path]), {"operations": ["migration"], "require": ["runbook"]})
    assert result["consumption"]["status"] == "blocked"
    assert result["guidance"] == []
    assert "required-context-unavailable" in {i["code"] for i in result["unresolved"]}


def test_missing_cross_repository_contract_blocks_only_dependent_context(tmp_path):
    document(tmp_path)
    result = resolve_context(build_catalog([tmp_path]), {"operations": ["migration"], "required_sources": ["consumer-contract"]})
    assert result["consumption"]["status"] == "blocked"
    assert result["background"]  # investigation still has useful material
    assert result["consumption"]["required"][0]["reasons"] == ["missing"]


def test_wrong_component_or_unchecked_environment_cannot_satisfy_obligation(tmp_path):
    document(tmp_path, scope="components: [billing]")
    result = resolve_context(build_catalog([tmp_path]), {"components": ["reporting"], "required_sources": ["migration-guide"]})
    assert result["consumption"]["required"][0]["reasons"] == ["inapplicable"]
    document(tmp_path, scope="operations: [migration]\n  environments: [production]")
    result = resolve_context(build_catalog([tmp_path]), {"operations": ["migration"]})
    assert result["consumption"]["required"][0]["reasons"] == ["applicability-unchecked"]


def test_exclusion_is_discovery_policy_not_permission(tmp_path):
    document(tmp_path, mode="excluded-by-default")
    catalog = build_catalog([tmp_path])
    assert resolve_context(catalog, {"topic": "prerequisite details", "operations": ["migration"]})["sources"] == []
    assert search(catalog, "prerequisite details")["results"]
    assert resolve_context(catalog, {"operations": ["migration"], "include_sources": ["migration-guide"]})["sources"]
    # Unavailable caller-readable roots cannot be replaced by catalog metadata.
    empty = build_catalog([tmp_path / "inaccessible-or-absent"])
    result = resolve_context(empty, {"required_sources": ["migration-guide"]})
    assert result["sources"] == []
    assert result["consumption"]["status"] == "blocked"


def test_required_background_change_and_disappearance_need_review(tmp_path):
    path = document(tmp_path)
    request = {"operations": ["migration"]}
    manifest = resolve_context(build_catalog([tmp_path]), request)
    path.write_text(path.read_text() + "\nChanged prerequisite.\n")
    assert recheck(build_catalog([tmp_path]), manifest)["status"] == "review-required"
    path.unlink()
    result = recheck(build_catalog([tmp_path]), manifest)
    assert result["status"] == "review-required"


def test_bootstrap_is_compact_opt_in_and_respects_scope(tmp_path):
    document(tmp_path, mode="bootstrap", scope="environments: [production]")
    assert resolve_context(build_catalog([tmp_path]), {"environments": ["production"]})["sources"]
    result = resolve_context(build_catalog([tmp_path]), {"environments": ["test"]})
    assert result["consumption"]["required"] == []


def test_sidecar_consumption_change_needs_review_even_when_source_bytes_unchanged(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# Orientation\n")
    sidecar = tmp_path / "knowledge.toml"
    sidecar.write_text('[[documents]]\npath = "guide.md"\ndoc_id = "orientation-doc"\npurpose = "explanation"\nlifecycle = "ratified"\nconsumption = "bootstrap"\n')
    manifest = resolve_context(build_catalog([tmp_path]), {"topic": "orientation"})
    assert manifest["sources"][0]["required"]
    sidecar.write_text(sidecar.read_text().replace('"bootstrap"', '"discoverable"'))
    result = recheck(build_catalog([tmp_path]), manifest)
    assert result["status"] == "review-required"
    assert any(c["change"] == "consumption" for c in result["changes"])


@pytest.mark.parametrize("scope", ["components: [billing]", "repos: [billing]"])
def test_unspecified_bootstrap_scope_blocks_readiness(tmp_path, scope):
    document(tmp_path, mode="bootstrap", scope=scope)
    result = resolve_context(build_catalog([tmp_path]), {})
    assert result["consumption"]["status"] == "blocked"
    assert result["consumption"]["required"][0]["reasons"] == ["applicability-unchecked"]


def test_wildcard_operation_requires_concrete_operation(tmp_path):
    document(tmp_path, scope="operations: ['*']")
    catalog = build_catalog([tmp_path])
    assert resolve_context(catalog, {})["consumption"]["required"] == []
    assert resolve_context(catalog, {"operations": ["migration"]})["consumption"]["required"][0]["status"] == "ready"


def test_required_question_cannot_use_unchecked_or_proposed_authority(tmp_path):
    document(tmp_path, mode="discoverable", scope="environments: [production]")
    (tmp_path / "knowledge.toml").write_text('[[authorities]]\nquestion = "migration-policy"\nsource = "migration-guide"\n')
    result = resolve_context(build_catalog([tmp_path]), {"questions": ["migration-policy"]})
    assert result["consumption"]["status"] == "blocked"
    assert result["consumption"]["required"][0]["reasons"] == ["applicability-unchecked"]
    document(tmp_path, mode="discoverable", lifecycle="proposed", scope="environments: [production]")
    result = resolve_context(build_catalog([tmp_path]), {"questions": ["migration-policy"], "environments": ["production"]})
    assert result["consumption"]["status"] == "blocked"
    assert "not-effective-guidance" in result["consumption"]["required"][0]["reasons"]


def test_required_purpose_rejects_unchecked_guidance_and_binds_valid_choice(tmp_path):
    path = document(tmp_path, mode="discoverable", purpose="runbook", scope="environments: [production]")
    request = {"topic": "context prerequisite details", "require": ["runbook"]}
    result = resolve_context(build_catalog([tmp_path]), request)
    assert result["consumption"]["status"] == "blocked"
    assert "missing-guidance" in {i["code"] for i in result["unresolved"]}
    request["environments"] = ["production"]
    result = resolve_context(build_catalog([tmp_path]), request)
    assert result["consumption"]["status"] == "ready"
    assert result["sources"][0]["required"]
    path.write_text(path.read_text() + "\nChanged obligation.\n")
    assert recheck(build_catalog([tmp_path]), result)["status"] == "review-required"


def test_required_metadata_and_provenance_changes_need_review(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# Orientation\n")
    sidecar = tmp_path / "knowledge.toml"
    sidecar.write_text('[[documents]]\npath = "guide.md"\ndoc_id = "orientation-doc"\npurpose = "explanation"\nlifecycle = "ratified"\nconsumption = "bootstrap"\neffective = "2026-10-01"\n')
    request = {"as_of": "2026-10-15"}
    manifest = resolve_context(build_catalog([tmp_path]), request)
    sidecar.write_text(sidecar.read_text().replace('2026-10-01', '2027-01-01'))
    result = recheck(build_catalog([tmp_path]), manifest)
    assert result["status"] == "review-required"
    assert any(c["change"] == "metadata" for c in result["changes"])
    assert result["current"]["consumption"]["status"] == "blocked"
    sidecar.write_text(sidecar.read_text().replace('2027-01-01', '2026-10-01').replace('guide.md', 'moved.md'))
    path.rename(tmp_path / "moved.md")
    result = recheck(build_catalog([tmp_path]), manifest)
    assert result["status"] == "review-required"
    assert any(c["change"] == "provenance" for c in result["changes"])


def test_required_purpose_rejects_draft_observation(tmp_path):
    document(tmp_path, mode="discoverable", purpose="observation", lifecycle="draft", scope="environments: [production]")
    result = resolve_context(build_catalog([tmp_path]), {"topic": "context prerequisite details", "environments": ["production"], "require": ["observation"]})
    assert result["consumption"]["status"] == "blocked"
    assert "missing-guidance" in {i["code"] for i in result["unresolved"]}


def test_older_v1_manifest_without_metadata_requires_refresh_for_mandatory_source(tmp_path):
    from vuoro_knowledge.resolve import manifest_digest

    document(tmp_path, mode="bootstrap", scope="environments: [production]")
    catalog = build_catalog([tmp_path])
    manifest = resolve_context(catalog, {"environments": ["production"]})
    manifest["sources"][0].pop("metadata")
    manifest["manifest_digest"] = manifest_digest(manifest)
    result = recheck(catalog, manifest)
    assert result["status"] == "review-required"
    assert any(c["change"] == "metadata" for c in result["changes"])
