"""maintenance-handover-v1: exact private bytes and bounded C0 fixture checks.

These are reproducible profile-specific observations, not a general truth oracle.
No model output is ever turned into authorization or a completion decision here.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path

PROFILE = "maintenance-handover-v1"
SOURCE_DIGEST = (
    "sha256:ee7c6bc4670731b054494e14487a0232e7e3b2990255fa477026570472ec4e12"
)
# A deliberately bounded extraction profile. Free-form claims cannot become
# passed verification merely by including an uncertainty keyword.
STATEMENTS = {
    "M1": {
        "unknown": "The cause of the dripping tap is unknown; seals and pipes have not been inspected.",
        "next_check": "Arrange an inspection of the tap seals and pipes.",
    },
    "M2": {
        "unknown": "The cause of the flickering light is unknown; no electrical assessment is available.",
        "next_check": "Arrange an electrician assessment of the flickering light.",
    },
    "M3": {
        "unknown": "The effect of filter cleaning on airflow is unknown; comparative measurements are unavailable.",
        "next_check": "Arrange an airflow assessment; do not claim a measured improvement.",
    },
}


def instructions() -> str:
    return (
        "Return only JSON with keys observations and outcomes_verified (false). "
        "observations must contain exactly one row per M1, M2, M3 with keys source_id, fact, unknown, next_check, owner. "
        "Copy each complete source text exactly into fact; set owner to unassigned. "
        "For unknown and next_check use exactly these approved source-specific statements: "
        + json.dumps(STATEMENTS)
        + ". No other text, claims, planning or markdown."
    )


class VerificationRefused(ValueError):
    pass


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def revision() -> str:
    return sha(Path(__file__).read_bytes())


def verify(source: bytes, artifact: bytes, manifest: dict) -> dict:
    if sha(source) != SOURCE_DIGEST:
        raise VerificationRefused(
            "This profile accepts only the pinned maintenance source fixture"
        )
    if (
        sha(source) != manifest["source_digest"]
        or sha(artifact) != manifest["artifact_digest"]
    ):
        raise VerificationRefused("Exact source/private artifact digest mismatch")
    fixture = json.loads(source)
    if fixture.get("synthetic") is not True or fixture.get("classification") != "C0":
        raise VerificationRefused(
            "This profile qualifies only its public source fixture"
        )
    result = json.loads(artifact)
    if (
        result.get("synthetic")
        or result.get("kind") != "agent-result"
        or result.get("finish_reason") != "stop"
        or result.get("generation") != manifest["generation"]
    ):
        raise VerificationRefused(
            "Private result is not a complete result of the bound configuration"
        )
    try:
        answer = json.loads(result["answer"])
        if (
            set(answer) != {"observations", "outcomes_verified"}
            or answer["outcomes_verified"] is not False
        ):
            raise ValueError
        rows = answer["observations"]
        if len(rows) != len(fixture["snippets"]):
            raise ValueError
        sources = {s["id"]: s["text"] for s in fixture["snippets"]}
        if len({r["source_id"] for r in rows}) != len(sources):
            raise ValueError
        for row in rows:
            if set(row) != {"source_id", "fact", "unknown", "next_check", "owner"}:
                raise ValueError
            if (
                row["source_id"] not in sources
                or row["fact"] != sources[row["source_id"]]
            ):
                raise ValueError
            if row["owner"] != "unassigned":
                raise ValueError
            if any(row[k] != v for k, v in STATEMENTS[row["source_id"]].items()):
                raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise VerificationRefused(
            "The handover does not satisfy the pinned source/uncertainty/action profile"
        ) from None
    return {
        "profile": PROFILE,
        "profile_revision": revision(),
        "source_digest": sha(source),
        "artifact_digest": sha(artifact),
        "source_ids": sorted(sources),
        "checks": [
            {"name": name, "revision": revision(), "status": "passed"}
            for name in (
                "private-artifact-integrity",
                "source-and-handover-contract",
                "receipt-manifest-binding",
            )
        ],
    }
