"""Bind a context manifest to work through the existing evidence path.

No new store. The manifest is an immutable artifact; the work owner records a
reference to it:

* offline / Git path: ``sprintctl event observation add --type work.completed
  --evidence-ref <ref>`` with an ``artifact`` ref whose revision is the
  ``sha256:`` digest of the manifest bytes;
* served path: a ``work.evidence.append-v1`` item draft. Chain position,
  run binding and idempotency belong to the append owner and are left out.

Both attest which sources were supplied, never that an agent understood or
followed them. For reconstruction the cited Git revisions must stay
retrievable; a digest alone does not bring the content back.
"""

from __future__ import annotations

from typing import Any

from . import __version__
from .catalog import sha256_hex

EVIDENCE_KIND = "knowledge-context-manifest"
COLLECTOR = f"vuoro-knowledge/{__version__}"


def evidence_ref(manifest_bytes: bytes, source: str) -> dict[str, str]:
    """A Sprintctl ``EvidenceRef`` (kind ``artifact``) for the written manifest."""

    return {"kind": "artifact", "source": source, "revision": "sha256:" + sha256_hex(manifest_bytes)}


def evidence_item_draft(manifest: dict[str, Any], manifest_bytes: bytes, ref: str, *, item_id: str, generated_at: str) -> dict[str, Any]:
    """Fields of a ``work.evidence.append-v1`` item that this collector owns."""

    component_digests = {
        f"{s['repo']}:{s['path']}": f"sha256:{s['sha256']}" for s in manifest["sources"]
    }
    return {
        "item_id": item_id,
        "kind": EVIDENCE_KIND,
        "ref": ref,
        "digest": "sha256:" + sha256_hex(manifest_bytes),
        "collector": COLLECTOR,
        "validity": {
            "basis": "until_inputs_change",
            "valid_from": generated_at,
            "valid_until": None,
            "component_digests": component_digests,
        },
        "claims": [
            {
                "claim_type": "observation",
                "subject": "knowledge-context-supplied",
                "grant_id": None,
                "freshness": None,
                "confirms": None,
                "detail": {
                    "manifest_digest": manifest["manifest_digest"],
                    "catalog_digest": manifest["catalog"]["digest"],
                    "sources": len(manifest["sources"]),
                    "conflicts": len(manifest["conflicts"]),
                    "unresolved": [u["code"] for u in manifest["unresolved"]],
                },
            }
        ],
        "provenance": {
            "resolver": manifest["resolver"],
            "request": manifest["request"],
            "roots": manifest["catalog"]["roots"],
            "attests": manifest["attests"],
        },
    }
