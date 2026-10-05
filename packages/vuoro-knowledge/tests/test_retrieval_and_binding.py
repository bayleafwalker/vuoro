from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vuoro_knowledge.catalog import build_catalog
from vuoro_knowledge.cli import main
from vuoro_knowledge.evidence import evidence_item_draft, evidence_ref
from vuoro_knowledge.resolve import resolve_context
from vuoro_knowledge.retrieval import get, search

from knowledge_fixtures import estate, write  # noqa: F401  (pytest fixtures)

def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


RUNBOOK = """---
doc_id: rb-doc
purpose: runbook
lifecycle: ratified
ratified_at: 2026-09-01
applies_to:
  environments: [poc]
---

# Rotate the gateway token

## Prerequisites

Tunnel up.

## Verification

Read the running pod imageID.

## Failure handling

Roll back.
"""


def test_search_explains_matches_and_honours_filters(estate):
    catalog = build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]])
    result = search(catalog, "kctl knowledge")
    top = result["results"][0]
    assert top["doc_id"] == "kctl-vuoro-served-knowledge-alignment"
    assert "title" in top["match"]["fields"]
    filtered = search(catalog, "kctl", filters={"purpose": ["proposal"]})
    assert [r["doc_id"] for r in filtered["results"]] == ["agentops-s4-evidence-home-preparation"]


def test_get_section_keeps_the_notices(tmp_path, write):
    write("r/rb.md", RUNBOOK)
    catalog = build_catalog([tmp_path / "r"])
    result = get(catalog, "rb-doc", section="verification")
    assert result["content"].startswith("## Verification")
    assert "Roll back" not in result["content"]
    assert {"applicability", "declared-lifecycle"} <= {n["code"] for n in result["notices"]}


def test_get_is_bounded(tmp_path, write):
    write("r/rb.md", RUNBOOK)
    result = get(build_catalog([tmp_path / "r"]), "rb-doc", max_lines=3)
    assert result["truncated"] is True and len(result["content"].splitlines()) == 3


def test_exact_revision_is_retrievable_after_the_file_changes(tmp_path, write):
    root = tmp_path / "r"
    write("r/rb.md", RUNBOOK)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "one")
    first = git(root, "rev-parse", "HEAD")
    catalog = build_catalog([root])
    original = catalog.documents["rb-doc"].source_ref()
    assert original["revision"] == first and original["dirty"] is False and original["blob"]

    (root / "rb.md").write_text(RUNBOOK.replace("Roll back.", "Escalate."))
    git(root, "commit", "-qam", "two")
    result = get(build_catalog([root]), "rb-doc", revision=first, section="failure-handling")
    assert "Roll back." in result["content"]
    assert result["source"]["sha256"] == original["sha256"]


def test_dirty_source_is_disclosed(tmp_path, write):
    root = tmp_path / "r"
    write("r/rb.md", RUNBOOK)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "one")
    (root / "rb.md").write_text(RUNBOOK + "\nlocal edit\n")
    result = resolve_context(build_catalog([root]), {"topic": "rotate gateway token", "environments": ["poc"], "as_of": "2026-10-05"})
    assert any(w["code"] == "uncommitted-source" for w in result["warnings"])


def test_evidence_binding_uses_existing_shapes(estate):
    catalog = build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]])
    manifest = resolve_context(catalog, {"subjects": ["knowledge-resolution"], "as_of": "2026-10-05"})
    data = json.dumps(manifest).encode()
    ref = evidence_ref(data, "artifact://manifests/m.json")
    assert ref["kind"] == "artifact" and ref["revision"].startswith("sha256:")
    item = evidence_item_draft(manifest, data, "artifact://manifests/m.json", item_id="ev-1", generated_at="2026-10-05T00:00:00Z")
    assert item["validity"]["basis"] == "until_inputs_change"
    assert set(item) == {"item_id", "kind", "ref", "digest", "collector", "validity", "claims", "provenance"}
    assert "transition-not-evidenced" in item["claims"][0]["detail"]["unresolved"]


def test_cli_resolve_and_recheck(estate, tmp_path, capsys):
    roots = [arg for name in ("vuoro", "kctl", "agentops") for arg in ("--root", str(estate[name]))]
    out = tmp_path / "manifest.json"
    assert main(["resolve", *roots, "--subject", "knowledge-resolution", "--as-of", "2026-10-05", "--out", str(out), "--item-id", "ev-1"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["evidence_ref"]["source"] == str(out)
    assert "disposition-changed" in summary["conflicts"]
    assert main(["recheck", *roots, str(out)]) == 0
    capsys.readouterr()
    plan = estate["kctl"] / "docs/plans/vuoro-served-knowledge-alignment.md"
    plan.write_text(plan.read_text() + "\nAmended.\n")
    assert main(["recheck", *roots, str(out)]) == 3


def test_cli_validate_exit_code(tmp_path, write, capsys):
    write("r/a.md", "---\ndoc_id: a-doc\npurpose: wiki\n---\n")
    assert main(["validate", "--root", str(tmp_path / "r")]) == 1


def test_tampered_manifest_is_refused(estate):
    from vuoro_knowledge.resolve import recheck

    catalog = build_catalog([estate["vuoro"], estate["kctl"], estate["agentops"]])
    manifest = resolve_context(catalog, {"subjects": ["knowledge-resolution"], "as_of": "2026-10-05"})
    manifest["conflicts"] = []
    with pytest.raises(ValueError):
        recheck(catalog, manifest)
