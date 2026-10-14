"""Workspace export (``vuoro-workspace-export/v1`` directory bundle) and import."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

from . import errors as E
from .errors import WorkspaceError
from .extraction import EXTRACTOR, EXTRACTOR_VERSION, extract
from .ids import is_ulid, new_ulid, normalize, request_digest
from .objectstore import artifact_key

if TYPE_CHECKING:
    from .store import Principal, WorkspaceStore

EXPORT_FORMAT = "vuoro-workspace-export/v1"
MANIFEST = "manifest.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_workspace(store: "WorkspaceStore", p: "Principal", ws: str, dest: str) -> dict[str, Any]:
    from .store import ADMIN

    root = Path(dest)
    if root.exists() and any(root.iterdir()):
        raise WorkspaceError(E.INVALID, "export destination must be empty")
    with store.conn.transaction(), store.conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        store._authorize(cur, p, ws, ADMIN)
        cur.execute("SELECT workspace_id, tenant_id, name, created_at, origin FROM workspace WHERE workspace_id = %s", (ws,))
        workspace = cur.fetchone()
        snapshot_seq = store._watermark(cur, ws)
        cur.execute("SELECT * FROM knowledge_item WHERE workspace_id = %s ORDER BY item_id", (ws,))
        items = cur.fetchall()
        cur.execute("SELECT * FROM knowledge_revision WHERE workspace_id = %s ORDER BY created_at, revision_id", (ws,))
        revisions = cur.fetchall()
        cur.execute("SELECT * FROM proposal WHERE workspace_id = %s ORDER BY created_at, proposal_id", (ws,))
        proposals = cur.fetchall()
        cur.execute("SELECT * FROM artifact WHERE workspace_id = %s ORDER BY artifact_id", (ws,))
        artifacts = cur.fetchall()
        cur.execute("SELECT * FROM artifact_version WHERE workspace_id = %s ORDER BY created_at, version_id", (ws,))
        versions = cur.fetchall()
        cur.execute("SELECT * FROM relation WHERE workspace_id = %s ORDER BY relation_id", (ws,))
        relations = cur.fetchall()

    live_items = {i["item_id"] for i in items if i["deleted_at"] is None}
    live_artifacts = {a["artifact_id"] for a in artifacts if a["deleted_at"] is None}
    tombstones = [{"kind": "item", "id": i["item_id"], "deleted_at": i["deleted_at"]} for i in items if i["deleted_at"]]
    tombstones += [{"kind": "artifact", "id": a["artifact_id"], "deleted_at": a["deleted_at"]}
                   for a in artifacts if a["deleted_at"]]
    root.mkdir(parents=True, exist_ok=True)

    revs_by_item: dict[str, list[dict[str, Any]]] = {}
    exported_revisions: set[str] = set()
    for rev in revisions:
        if rev["item_id"] not in live_items:
            continue
        path = f"items/{rev['item_id']}/{rev['revision_id']}.md"
        data = rev["body_md"].encode()
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
        exported_revisions.add(rev["revision_id"])
        revs_by_item.setdefault(rev["item_id"], []).append({
            "revision_id": rev["revision_id"], "parent_revision_id": rev["parent_revision_id"],
            "title": rev["title"], "path": path, "content_sha256": rev["content_sha256"],
            "author_id": rev["author_id"], "sources": rev["sources"], "state": rev["state"],
            "reviewed_by": rev["reviewed_by"], "reviewed_at": rev["reviewed_at"], "created_at": rev["created_at"],
        })

    missing: list[dict[str, Any]] = []
    vers_by_artifact: dict[str, list[dict[str, Any]]] = {}
    exported_versions: set[str] = set()
    for v in versions:
        if v["artifact_id"] not in live_artifacts:
            continue
        path = None
        if v["processing_state"] == "ready":
            data = store.objects.get(v["object_key"])
            if data is None or _sha(data) != v["sha256"] or len(data) != v["size_bytes"]:
                missing.append({"kind": "artifact_version", "version_id": v["version_id"],
                                "reason": "object-missing" if data is None else "object-corrupt"})
            else:
                path = f"artifacts/{v['artifact_id']}/{v['version_id']}"
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(data)
        exported_versions.add(v["version_id"])
        vers_by_artifact.setdefault(v["artifact_id"], []).append({
            "version_id": v["version_id"], "sha256": v["sha256"] or v["expected_sha256"],
            "size": v["size_bytes"] if v["size_bytes"] is not None else v["expected_size"],
            "media_type": v["media_type"] or v["declared_media_type"], "path": path,
            "processing_state": v["processing_state"], "review_state": v["review_state"],
            "extraction_state": v["extraction_state"], "created_by": v["created_by"], "created_at": v["created_at"],
            "reviewed_by": v["reviewed_by"], "reviewed_at": v["reviewed_at"],
        })

    exported = exported_revisions | exported_versions
    manifest = normalize({
        "format": EXPORT_FORMAT,
        "workspace": {"workspace_id": ws, "tenant_id": workspace["tenant_id"], "name": workspace["name"],
                      "created_at": workspace["created_at"], "origin": workspace["origin"]},
        "snapshot_seq": snapshot_seq,
        "items": [{"item_id": i["item_id"], "approved_revision_id": i["approved_revision_id"],
                   "latest_revision_id": i["latest_revision_id"], "created_by": i["created_by"],
                   "created_at": i["created_at"], "revisions": revs_by_item.get(i["item_id"], [])}
                  for i in items if i["item_id"] in live_items],
        "artifacts": [{"artifact_id": a["artifact_id"], "name": a["name"], "approved_version_id": a["approved_version_id"],
                       "latest_version_id": a["latest_version_id"], "created_by": a["created_by"],
                       "created_at": a["created_at"], "versions": vers_by_artifact.get(a["artifact_id"], [])}
                      for a in artifacts if a["artifact_id"] in live_artifacts],
        "proposals": [{k: pr[k] for k in ("proposal_id", "item_id", "revision_id", "expected_base_revision_id",
                                           "rationale", "evidence", "author_id", "state", "reviewer_id",
                                           "review_note", "decided_at", "created_at")}
                      for pr in proposals if pr["item_id"] in live_items],
        "relations": [{k: r[k] for k in ("relation_id", "kind", "source_kind", "source_id", "target_kind",
                                          "target_id", "target_ref", "created_by", "created_at")}
                      for r in relations
                      if r["source_id"] in exported and (r["target_id"] is None or r["target_id"] in exported)],
        "publication_history": publication_history_from(proposals, versions, live_items, live_artifacts),
        "missing": missing,
        "tombstones": tombstones,
    })
    raw = json.dumps(manifest, indent=2, sort_keys=True).encode()
    (root / MANIFEST).write_bytes(raw)
    with store.conn.transaction(), store.conn.cursor() as cur:
        store._audit(cur, ws, p.principal_id, "workspace.exported", "workspace", ws, "ok",
                     {"snapshot_seq": snapshot_seq, "manifest_sha256": _sha(raw)})
    return {"workspace_id": ws, "path": str(root), "snapshot_seq": snapshot_seq, "manifest_sha256": _sha(raw),
            "counts": {"items": len(manifest["items"]), "revisions": len(exported_revisions),
                       "artifacts": len(manifest["artifacts"]), "versions": len(exported_versions),
                       "missing": len(missing)}}


def publication_history_from(proposals: list[dict[str, Any]], versions: list[dict[str, Any]],
                             live_items: set[str], live_artifacts: set[str]) -> list[dict[str, Any]]:
    history = [
        {"target_kind": "revision", "target_id": pr["revision_id"], "proposal_id": pr["proposal_id"],
         "decision": pr["state"], "actor_id": pr["reviewer_id"] or pr["author_id"], "decided_at": pr["decided_at"]}
        for pr in proposals if pr["state"] != "open" and pr["item_id"] in live_items
    ]
    history += [
        {"target_kind": "artifact_version", "target_id": v["version_id"], "proposal_id": None,
         "decision": v["review_state"], "actor_id": v["reviewed_by"], "decided_at": v["reviewed_at"]}
        for v in versions if v["review_state"] != "unreviewed" and v["artifact_id"] in live_artifacts
    ]
    return sorted(normalize(history), key=lambda h: (h["decided_at"] or "", h["target_id"]))


# --------------------------------------------------------------------- import


def _load_bundle(root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Parse and fully validate a bundle before anything is written."""
    try:
        manifest = json.loads((root / MANIFEST).read_bytes())
    except (OSError, ValueError) as exc:
        raise WorkspaceError(E.IMPORT_INVALID, f"unreadable manifest: {exc}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT:
        raise WorkspaceError(E.IMPORT_INVALID, f"manifest format must be {EXPORT_FORMAT}")
    files: dict[str, bytes] = {}
    problems: list[str] = []

    def read(path: Any) -> bytes | None:
        if not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts:
            problems.append(f"unsafe path {path!r}")
            return None
        try:
            return (root / path).read_bytes()
        except OSError:
            problems.append(f"missing file {path}")
            return None

    revision_ids: set[str] = set()
    version_ids: set[str] = set()
    for item in manifest.get("items", []):
        revs = item.get("revisions", [])
        ids = {r.get("revision_id") for r in revs}
        for rev in revs:
            if not is_ulid(rev.get("revision_id")) or rev["revision_id"] in revision_ids:
                problems.append(f"bad or duplicate revision id {rev.get('revision_id')!r}")
            revision_ids.add(rev.get("revision_id"))
            data = read(rev.get("path"))
            if data is not None:
                if _sha(data) != rev.get("content_sha256"):
                    problems.append(f"checksum mismatch for revision {rev['revision_id']}")
                files[rev["path"]] = data
            if rev.get("parent_revision_id") is not None and rev["parent_revision_id"] not in ids:
                problems.append(f"revision {rev['revision_id']} has a parent outside its item")
        for pointer in ("approved_revision_id", "latest_revision_id"):
            if item.get(pointer) is not None and item[pointer] not in ids:
                problems.append(f"item {item.get('item_id')} {pointer} not in bundle")
    missing = {m.get("version_id") for m in manifest.get("missing", [])}
    for art in manifest.get("artifacts", []):
        vers = art.get("versions", [])
        ids = {v.get("version_id") for v in vers}
        for v in vers:
            if not is_ulid(v.get("version_id")) or v["version_id"] in version_ids:
                problems.append(f"bad or duplicate version id {v.get('version_id')!r}")
            version_ids.add(v.get("version_id"))
            if v.get("path") is not None:
                data = read(v["path"])
                if data is not None:
                    if _sha(data) != v.get("sha256") or len(data) != v.get("size"):
                        problems.append(f"checksum or size mismatch for version {v['version_id']}")
                    files[v["path"]] = data
            elif v.get("processing_state") == "ready" and v["version_id"] not in missing:
                problems.append(f"ready version {v['version_id']} has no file and no missing entry")
        for pointer in ("approved_version_id", "latest_version_id"):
            if art.get(pointer) is not None and art[pointer] not in ids:
                problems.append(f"artifact {art.get('artifact_id')} {pointer} not in bundle")
    for pr in manifest.get("proposals", []):
        if pr.get("revision_id") not in revision_ids or (
            pr.get("expected_base_revision_id") is not None and pr["expected_base_revision_id"] not in revision_ids
        ):
            problems.append(f"proposal {pr.get('proposal_id')} references a revision outside the bundle")
    known = revision_ids | version_ids
    for rel in manifest.get("relations", []):
        if rel.get("source_id") not in known or (rel.get("target_kind") != "repository_document"
                                                  and rel.get("target_id") not in known):
            problems.append(f"relation {rel.get('relation_id')} references an id outside the bundle")
    if problems:
        raise WorkspaceError(E.IMPORT_INVALID, "bundle failed validation", problems=problems[:50])
    return manifest, files


def _topological(revs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {r["revision_id"]: r for r in revs}
    ordered: list[dict[str, Any]] = []
    done: set[str] = set()

    def visit(rev: dict[str, Any], depth: int = 0) -> None:
        if rev["revision_id"] in done:
            return
        if depth > len(revs):
            raise WorkspaceError(E.IMPORT_INVALID, "revision parent cycle")
        parent = rev.get("parent_revision_id")
        if parent is not None:
            visit(by_id[parent], depth + 1)
        done.add(rev["revision_id"])
        ordered.append(rev)

    for rev in revs:
        visit(rev)
    return ordered


def import_workspace(store: "WorkspaceStore", p: "Principal", src: str, *, name: str | None,
                     tenant_id: str, idempotency_key: str | None) -> dict[str, Any]:
    from .store import ADMIN, GLOBAL_SCOPE

    if ADMIN not in p.authorities or p.is_agent:
        raise WorkspaceError(E.FORBIDDEN, f"import needs {ADMIN} and a non-agent principal")
    manifest, files = _load_bundle(Path(src))
    ws_name = name or manifest["workspace"].get("name") or "imported"
    request = {"manifest_digest": request_digest("manifest", manifest), "name": ws_name, "tenant_id": tenant_id}

    def run(cur: Any, _: Any) -> dict[str, Any]:
        ws = new_ulid()
        origin = {"format": EXPORT_FORMAT, "source_workspace_id": manifest["workspace"].get("workspace_id"),
                  "snapshot_seq": manifest.get("snapshot_seq"), "imported_at": store.now(),
                  "imported_by": p.principal_id}
        store._insert_workspace(cur, ws, tenant_id, ws_name, p.principal_id, normalize(origin))
        mapping: dict[str, dict[str, str]] = {"workspace": {origin["source_workspace_id"]: ws}, "item": {},
                                              "revision": {}, "proposal": {}, "artifact": {}, "artifact_version": {},
                                              "relation": {}}
        rmap, vmap = mapping["revision"], mapping["artifact_version"]
        for item in manifest.get("items", []):
            iid = mapping["item"][item["item_id"]] = new_ulid()
            for rev in item.get("revisions", []):
                rmap[rev["revision_id"]] = new_ulid()
            cur.execute(
                "INSERT INTO knowledge_item (item_id, workspace_id, approved_revision_id, latest_revision_id, "
                "created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (iid, ws, rmap.get(item.get("approved_revision_id")), rmap.get(item.get("latest_revision_id")),
                 item.get("created_by") or p.principal_id, item.get("created_at") or store.now()),
            )
            for rev in _topological(item.get("revisions", [])):
                body = files[rev["path"]].decode("utf-8")
                store._insert_revision(
                    cur, ws, iid, rmap[rev["revision_id"]], rmap.get(rev.get("parent_revision_id")), rev["title"],
                    body, rev["author_id"], rev.get("sources") or [], state=rev["state"],
                    reviewed_by=rev.get("reviewed_by"), reviewed_at=rev.get("reviewed_at"), created_at=rev.get("created_at"),
                )
        for pr in manifest.get("proposals", []):
            pid = mapping["proposal"][pr["proposal_id"]] = new_ulid()
            cur.execute(
                "INSERT INTO proposal (proposal_id, workspace_id, item_id, revision_id, expected_base_revision_id, "
                "rationale, evidence, author_id, state, reviewer_id, review_note, decided_at, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (pid, ws, mapping["item"][pr["item_id"]], rmap[pr["revision_id"]],
                 rmap.get(pr.get("expected_base_revision_id")), pr.get("rationale") or "",
                 Jsonb(pr.get("evidence") or []), pr["author_id"], pr["state"], pr.get("reviewer_id"),
                 pr.get("review_note"), pr.get("decided_at"), pr.get("created_at") or store.now()),
            )
        missing = {m["version_id"] for m in manifest.get("missing", [])}
        for art in manifest.get("artifacts", []):
            aid = mapping["artifact"][art["artifact_id"]] = new_ulid()
            for v in art.get("versions", []):
                vmap[v["version_id"]] = new_ulid()
            cur.execute(
                "INSERT INTO artifact (artifact_id, workspace_id, name, created_by, created_at) VALUES (%s,%s,%s,%s,%s)",
                (aid, ws, art["name"], art.get("created_by") or p.principal_id, art.get("created_at") or store.now()),
            )
            for v in art.get("versions", []):
                vid = vmap[v["version_id"]]
                key = artifact_key(ws, aid, vid)
                data = files.get(v["path"]) if v.get("path") else None
                state, review, extraction_state = v["processing_state"], v["review_state"], v["extraction_state"]
                reason = None
                if data is None and state == "ready":
                    state, review, reason = "rejected", "unreviewed", "missing-at-export"
                elif state in ("reserved", "verifying"):
                    state = "expired"
                extraction = None
                if data is not None and state == "ready":
                    store.objects.put(key, data)
                    extraction = extract(v["media_type"], data)
                    extraction_state = extraction.state
                result = normalize({"artifact_id": aid, "version_id": vid, "processing_state": state,
                                    "review_state": "unreviewed", "extraction_state": extraction_state,
                                    "sha256": v["sha256"], "size": v["size"], "media_type": v["media_type"],
                                    "reject_reasons": [reason] if reason else []})
                cur.execute(
                    "INSERT INTO artifact_version (version_id, workspace_id, artifact_id, object_key, expected_sha256, "
                    "expected_size, declared_media_type, sha256, size_bytes, media_type, processing_state, review_state, "
                    "extraction_state, reject_reason, finalize_result, created_by, created_at, expires_at, finalized_at, "
                    "reviewed_by, reviewed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (vid, ws, aid, key, v["sha256"], v["size"], v["media_type"],
                     v["sha256"] if state == "ready" else None, v["size"] if state == "ready" else None,
                     v["media_type"], state, review, extraction_state, reason,
                     Jsonb(result) if state in ("ready", "rejected") else None, v["created_by"],
                     v.get("created_at") or store.now(), v.get("created_at") or store.now(),
                     store.now() if state in ("ready", "rejected") else None, v.get("reviewed_by"), v.get("reviewed_at")),
                )
                if extraction is not None and extraction.state == "ready":
                    cur.execute(
                        "INSERT INTO extraction (extraction_id, workspace_id, version_id, extractor, extractor_version, "
                        "text_content, anchors, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (new_ulid(), ws, vid, EXTRACTOR, EXTRACTOR_VERSION, extraction.text,
                         Jsonb(extraction.anchors), store.now()),
                    )
            approved = art.get("approved_version_id")
            cur.execute(
                "UPDATE artifact SET approved_version_id = %s, latest_version_id = %s WHERE artifact_id = %s",
                (vmap[approved] if approved and approved not in missing else None,
                 vmap.get(art.get("latest_version_id")), aid),
            )
        for rel in manifest.get("relations", []):
            ids = {**rmap, **vmap}
            rid = mapping["relation"][rel["relation_id"]] = new_ulid()
            cur.execute(
                "INSERT INTO relation (relation_id, workspace_id, kind, source_kind, source_id, target_kind, target_id, "
                "target_ref, created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rid, ws, rel["kind"], rel["source_kind"], ids[rel["source_id"]], rel["target_kind"],
                 ids.get(rel["target_id"]) if rel.get("target_id") else None,
                 Jsonb(rel["target_ref"]) if rel.get("target_ref") is not None else None,
                 rel["created_by"], rel.get("created_at") or store.now()),
            )
        for kind, pairs in mapping.items():
            for source_id, local_id in pairs.items():
                cur.execute("INSERT INTO origin_map (workspace_id, entity_kind, source_id, local_id) VALUES (%s,%s,%s,%s)",
                            (ws, kind, source_id, local_id))
        store._emit(cur, ws, p.principal_id, "workspace.imported", "workspace", ws, visibility="published",
                    payload={"source_workspace_id": origin["source_workspace_id"],
                             "snapshot_seq": origin["snapshot_seq"]})
        return {"workspace_id": ws, "origin": origin, "origin_map": mapping,
                "counts": {k: len(v) for k, v in mapping.items()}}

    return store._mutate(p, GLOBAL_SCOPE, "import", ADMIN, request, idempotency_key, run, authorize=False)
