from __future__ import annotations

from vuoro_knowledge.catalog import build_catalog
from vuoro_knowledge.contract import parse_metadata, satisfies
from vuoro_knowledge.resolve import resolve_context
from vuoro_knowledge.validate import validate

from knowledge_fixtures import estate, write  # noqa: F401  (pytest fixtures)


def _codes(problems, severity=None):
    return {p.code for p in problems if severity is None or p.severity == severity}


def test_documents_without_doc_id_are_not_catalogued(tmp_path, write):
    write("repo/a.md", "# Just notes\n")
    write("repo/b.md", "---\ndoc_id: b-doc\npurpose: explanation\nlifecycle: draft\n---\n# B\n")
    catalog = build_catalog([tmp_path / "repo"])
    assert list(catalog.documents) == ["b-doc"]


def test_duplicate_ids_across_repositories(tmp_path, write):
    write("one/a.md", "---\ndoc_id: same\npurpose: decision\nlifecycle: draft\n---\n")
    write("two/a.md", "---\ndoc_id: same\npurpose: decision\nlifecycle: draft\n---\n")
    problems = validate(build_catalog([tmp_path / "one", tmp_path / "two"]))
    assert "duplicate-doc-id" in _codes(problems, "error")


def test_invalid_values_and_contradictory_lifecycle():
    _, problems = parse_metadata({"doc_id": "x-1", "purpose": "wiki", "lifecycle": "current"}, "t")
    assert {"invalid-purpose", "invalid-lifecycle"} <= _codes(problems)
    _, problems = parse_metadata({"doc_id": "x-1", "lifecycle": "draft", "status": "ratified"}, "t")
    assert "contradictory-lifecycle" in _codes(problems)


def test_legacy_status_maps_only_when_unambiguous():
    meta, problems = parse_metadata({"doc_id": "x-1", "status": "ratified"}, "t")
    assert meta.lifecycle == "ratified" and meta.lifecycle_source == "legacy-status"
    meta, problems = parse_metadata({"doc_id": "x-1", "status": "active"}, "t")
    assert meta.lifecycle is None and "unmapped-legacy-status" in _codes(problems)


def test_scalar_supersedes_is_one_reference_not_characters():
    meta, _ = parse_metadata({"doc_id": "x-1", "supersedes": "adr-001"}, "t")
    assert [r.target for r in meta.relations] == ["adr-001"]


def test_broken_reference_is_an_error_only_for_v1_documents(tmp_path, write):
    write("r/a.md", "---\ndoc_id: a-doc\npurpose: decision\nlifecycle: ratified\nsupersedes: [missing]\n---\n")
    write("r/b.md", "---\ndoc_id: b-doc\nstatus: ratified\nsupersedes: [some-concept]\n---\n")
    problems = validate(build_catalog([tmp_path / "r"]))
    errors = [(p.code, p.doc_id) for p in problems if p.severity == "error"]
    assert errors == [("broken-reference", "a-doc")]
    assert ("unresolved-legacy-reference", "b-doc") in [(p.code, p.doc_id) for p in problems]


def test_supersession_cycle(tmp_path, write):
    write("r/a.md", "---\ndoc_id: a-doc\npurpose: decision\nlifecycle: ratified\nsupersedes: [b-doc]\n---\n")
    write("r/b.md", "---\ndoc_id: b-doc\npurpose: decision\nlifecycle: ratified\nsupersedes: [a-doc]\n---\n")
    assert "supersession-cycle" in _codes(validate(build_catalog([tmp_path / "r"])), "error")


def test_a_proposal_cannot_supersede(tmp_path, write):
    write("r/old.md", "---\ndoc_id: old-doc\npurpose: decision\nlifecycle: ratified\n---\n# Old decision\n")
    write("r/new.md", "---\ndoc_id: new-doc\npurpose: proposal\nlifecycle: proposed\nsupersedes: [old-doc]\n---\n# New decision\n")
    catalog = build_catalog([tmp_path / "r"])
    assert "proposed-supersession" in _codes(validate(catalog), "warning")
    result = resolve_context(catalog, {"topic": "decision", "as_of": "2026-10-05"})
    old = next(r for r in result["governing"] if r["doc_id"] == "old-doc")
    assert old["proposed_supersession_by"] == ["new-doc"]
    assert [r["doc_id"] for r in result["historical"]] == []


def test_ratified_supersession_moves_the_old_document_to_history(tmp_path, write):
    write("r/old.md", "---\ndoc_id: old-doc\npurpose: decision\nlifecycle: ratified\n---\n# Old decision\n")
    write("r/new.md", "---\ndoc_id: new-doc\npurpose: decision\nlifecycle: ratified\nratified_at: 2026-09-01\nsupersedes: [old-doc]\n---\n# New decision\n")
    result = resolve_context(build_catalog([tmp_path / "r"]), {"topic": "decision", "as_of": "2026-10-05"})
    assert [r["doc_id"] for r in result["governing"]] == ["new-doc"]
    assert [(r["doc_id"], r["superseded_by"]) for r in result["historical"]] == [("old-doc", ["new-doc"])]


def test_fragment_supersession_is_partial(tmp_path, write):
    write("r/old.md", "---\ndoc_id: old-doc\npurpose: decision\nlifecycle: ratified\n---\n# Old decision\n")
    write("r/new.md", "---\ndoc_id: new-doc\npurpose: decision\nlifecycle: ratified\nratified_at: 2026-09-01\nsupersedes: ['old-doc:R1']\n---\n# New decision\n")
    result = resolve_context(build_catalog([tmp_path / "r"]), {"topic": "decision", "as_of": "2026-10-05"})
    assert sorted(r["doc_id"] for r in result["governing"]) == ["new-doc", "old-doc"]


def test_metadata_does_not_grant_itself_authority(tmp_path, write):
    write("r/reg.md", "---\ndoc_id: reg-doc\npurpose: decision\nlifecycle: ratified\n---\n# Register\n")
    write(
        "r/self.md",
        "---\ndoc_id: self-doc\npurpose: proposal\nlifecycle: proposed\n"
        "delegates:\n  - {question: component-status, source: reg-doc}\n---\n# Self\n",
    )
    catalog = build_catalog([tmp_path / "r"])
    assert "delegation-not-authoritative" in _codes(validate(catalog))
    result = resolve_context(catalog, {"questions": ["component-status"], "as_of": "2026-10-05"})
    assert result["authorities"] == []
    assert "no-authority" in {u["code"] for u in result["unresolved"]}


def test_conflicting_authorities_are_returned_not_chosen(tmp_path, write):
    write("r/a.md", "---\ndoc_id: reg-a\npurpose: decision\nlifecycle: ratified\n---\n")
    write("r/b.md", "---\ndoc_id: reg-b\npurpose: decision\nlifecycle: ratified\n---\n")
    write(
        "r/knowledge.toml",
        'schema = "vuoro-knowledge-repo/v1"\nrepo = "r"\n'
        '[[authorities]]\nquestion = "interface-contract"\nsource = "reg-a"\n'
        '[[authorities]]\nquestion = "interface-contract"\nsource = "reg-b"\n',
    )
    catalog = build_catalog([tmp_path / "r"])
    assert "conflicting-delegation" in _codes(validate(catalog), "error")
    result = resolve_context(catalog, {"questions": ["interface-contract"], "as_of": "2026-10-05"})
    assert result["authorities"] == []
    assert result["conflicts"][0]["code"] == "authority-conflict"
    assert result["conflicts"][0]["sources"] == ["reg-a", "reg-b"]


def test_observation_needs_a_date_and_only_observations_establish(tmp_path, write):
    write("r/o.md", "---\ndoc_id: obs-doc\npurpose: observation\nlifecycle: ratified\n---\n")
    write("r/d.md", "---\ndoc_id: dec-doc\npurpose: decision\nlifecycle: ratified\nestablishes: [{component: x, state: retired}]\n---\n")
    errors = _codes(validate(build_catalog([tmp_path / "r"])), "error")
    assert {"observation-undated", "establishes-not-observation"} <= errors


def test_sidecar_and_frontmatter_together_is_contradictory(tmp_path, write):
    write("r/a.md", "---\ndoc_id: a-doc\npurpose: decision\nlifecycle: draft\n---\n")
    write("r/knowledge.toml", 'schema = "vuoro-knowledge-repo/v1"\n[[documents]]\npath = "a.md"\ndoc_id = "a-doc"\npurpose = "decision"\n')
    assert "contradictory-declaration" in _codes(validate(build_catalog([tmp_path / "r"])), "error")


def test_version_specifiers():
    assert satisfies("0.1.45", ">=0.1.40,<0.1.50")
    assert not satisfies("0.1.59", ">=0.1.40,<0.1.50")
    assert satisfies("0.1.10", ">0.1.9")
    assert satisfies("0.1.0-poc.33", "<0.1.0")
    assert satisfies("0.1.0-poc.71", ">0.1.0-poc.9")
