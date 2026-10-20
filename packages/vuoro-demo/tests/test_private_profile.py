"""Falsifiers for the source contract; no model or authority is mocked as passed."""

import json
from pathlib import Path
import pytest
from vuoro_demo.private_profile import (
    VerificationRefused,
    revision,
    sha,
    verify,
    STATEMENTS,
)

SOURCE = (
    Path(__file__).resolve().parents[3]
    / "docs/fixtures/private-workflow-maintenance.json"
)


def sample():
    source = SOURCE.read_bytes()
    answer = {
        "observations": [
            {
                "source_id": s["id"],
                "fact": s["text"],
                **STATEMENTS[s["id"]],
                "owner": "unassigned",
            }
            for s in json.loads(source)["snippets"]
        ],
        "outcomes_verified": False,
    }
    artifact = {
        "kind": "agent-result",
        "finish_reason": "stop",
        "generation": "generation-fixture",
        "answer": json.dumps(answer),
    }
    return source, artifact, answer


def assess(source, artifact):
    raw = json.dumps(artifact).encode()
    return verify(
        source,
        raw,
        {
            "source_digest": sha(source),
            "artifact_digest": sha(raw),
            "generation": "generation-fixture",
        },
    )


def test_complete_bounded_source_contract():
    source, artifact, _ = sample()
    result = assess(source, artifact)
    assert result["source_ids"] == ["M1", "M2", "M3"]
    assert {c["revision"] for c in result["checks"]} == {revision()}


@pytest.mark.parametrize(
    "fault",
    [
        "planning",
        "missing-source",
        "wrong-source",
        "owner",
        "outcome",
        "invented-cause",
        "keyword-laundering",
        "truncated",
        "synthetic",
        "generation",
    ],
)
def test_unacceptable_output_cannot_be_verification(fault):
    source, artifact, answer = sample()
    if fault == "planning":
        artifact["answer"] = "I will now prepare the handover."
    if fault == "missing-source":
        answer["observations"].pop()
    if fault == "wrong-source":
        answer["observations"][0]["fact"] = "The leak was repaired."
    if fault == "owner":
        answer["observations"][0]["owner"] = "bayleaf"
    if fault == "outcome":
        answer["outcomes_verified"] = True
    if fault == "invented-cause":
        answer["observations"][0]["next_check"] = (
            "Inspect the seal, caused by corrosion."
        )
    if fault == "keyword-laundering":
        answer["observations"][0]["unknown"] = (
            "Cause unknown. A broken seal definitely caused this issue."
        )
    if fault == "truncated":
        artifact["finish_reason"] = "length"
    if fault == "synthetic":
        artifact["synthetic"] = True
    if fault == "generation":
        artifact["generation"] = "another-generation"
    if fault != "planning":
        artifact["answer"] = json.dumps(answer)
    with pytest.raises(VerificationRefused):
        assess(source, artifact)


@pytest.mark.parametrize("field", ["source_digest", "artifact_digest"])
def test_exact_bytes_must_match_manifest(field):
    source, artifact, _ = sample()
    raw = json.dumps(artifact).encode()
    manifest = {
        "source_digest": sha(source),
        "artifact_digest": sha(raw),
        "generation": "generation-fixture",
    }
    manifest[field] = "sha256:" + "0" * 64
    with pytest.raises(VerificationRefused):
        verify(source, raw, manifest)


def test_unrelated_sources_cannot_reuse_approved_handover_statements():
    source, artifact, _ = sample()
    fixture = json.loads(source)
    for row in fixture["snippets"]:
        row["text"] = "Unrelated roof observation."
    with pytest.raises(VerificationRefused, match="pinned maintenance"):
        assess(json.dumps(fixture).encode(), artifact)
