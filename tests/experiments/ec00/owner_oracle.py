"""Disposable SQLite transaction oracle; never a product API, schema, or store."""
from __future__ import annotations
import base64, hashlib, hmac, json, re, secrets, sqlite3
from pathlib import Path
from typing import Any, Callable
from artifact_semantics import verify_signed_artifact

class Conflict(Exception):
    """Stale revision, invalid transition, bad cursor, or key conflict."""
class ResponseLost(Exception):
    """Simulated transport loss after a committed owner transaction."""

def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def material_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()
def _reject_identity_claims(value: Any) -> None:
    forbidden = {"principal", "principal_id", "workspace", "workspace_id", "role", "scope", "authority"}
    if isinstance(value, dict):
        if forbidden.intersection(value): raise Conflict("caller-controlled identity or authority claims refused")
        for child in value.values(): _reject_identity_claims(child)
    elif isinstance(value, list):
        for child in value: _reject_identity_claims(child)

class OwnerOracle:
    """Small file-backed owner transaction oracle for transport experiments only."""
    def __init__(self, db: sqlite3.Connection, path: str, principal_id: str, workspace_id: str, role: str, max_read: int):
        self.db, self.path, self.principal_id, self.workspace_id, self.role, self.max_read = db, path, principal_id, workspace_id, role, max_read

    @classmethod
    def initialize(cls, path: str | Path, identities: list[dict[str, Any]], *, evidence: list[dict[str, Any]] = (), links: list[dict[str, Any]] = ()) -> None:
        path = str(path)
        if path == ":memory:": raise ValueError("file-backed DB path required")
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
              CREATE TABLE identity(principal_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)));
              CREATE TABLE evidence(evidence_ref TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, request_id TEXT NOT NULL, material_digest TEXT NOT NULL, outcome TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('eligible','revoked')));
              CREATE TABLE signed_artifact(workspace_id TEXT NOT NULL, request_id TEXT NOT NULL REFERENCES request(request_id), material_revision INTEGER NOT NULL, material_digest TEXT NOT NULL, artifact_digest TEXT NOT NULL, key_id TEXT NOT NULL, original_bytes BLOB NOT NULL, envelope_bytes BLOB NOT NULL, signature BLOB NOT NULL, PRIMARY KEY(request_id,material_revision));
              CREATE TABLE validated_link(link_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, owner_principal TEXT NOT NULL, repo_id TEXT NOT NULL, run_ref TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)));
              CREATE TABLE request(request_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, revision INTEGER NOT NULL, material_revision INTEGER NOT NULL, state TEXT NOT NULL, body_json TEXT NOT NULL, reported INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0, evidence_ref TEXT, artifact_decision_digest TEXT, created_seq INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE dependent(request_id TEXT NOT NULL REFERENCES request(request_id), dependent_id TEXT NOT NULL REFERENCES validated_link(link_id), active INTEGER NOT NULL DEFAULT 1, continuation_ack INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(request_id,dependent_id));
              CREATE TABLE idem(workspace_id TEXT NOT NULL, principal_id TEXT NOT NULL, idem_key TEXT NOT NULL, operation TEXT NOT NULL, body_hash TEXT NOT NULL, result_json TEXT NOT NULL, PRIMARY KEY(workspace_id,principal_id,idem_key));
              CREATE TABLE event(seq INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, request_id TEXT NOT NULL, action TEXT NOT NULL, revision INTEGER NOT NULL, event_json TEXT NOT NULL);
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            db.executemany("INSERT INTO identity VALUES(?,?,?,?)", [(i["principal_id"], i["workspace_id"], i["role"], int(i.get("active", True))) for i in identities])
            db.executemany("INSERT INTO evidence VALUES(?,?,?,?,?,?)", [(e["evidence_ref"], e["workspace_id"], e["request_id"], e["material_digest"], e["outcome"], e.get("status", "eligible")) for e in evidence])
            db.executemany("INSERT INTO validated_link VALUES(?,?,?,?,?,?)", [(l["link_id"], l["workspace_id"], l["owner_principal"], l["repo_id"], l["run_ref"], int(l.get("active", True))) for l in links])
            db.execute("INSERT INTO metadata VALUES('cursor_key',?)", (base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),))
            for workspace in sorted({i["workspace_id"] for i in identities}):
                db.execute("INSERT INTO metadata VALUES(?, '1')", (f"retained_from_seq:{workspace}",))
        finally: db.close()

    @classmethod
    def connect_as(cls, path: str | Path, principal_id: str, *, max_read: int = 2) -> "OwnerOracle":
        db = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        ident = db.execute("SELECT * FROM identity WHERE principal_id=?", (principal_id,)).fetchone()
        if ident is None or not ident["active"]:
            db.close(); raise Conflict("caller is not currently authorized")
        return cls(db, str(path), ident["principal_id"], ident["workspace_id"], ident["role"], max_read)

    def close(self) -> None: self.db.close()
    def _active_identity(self) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM identity WHERE principal_id=?", (self.principal_id,)).fetchone()
        if row is None or not row["active"] or row["workspace_id"] != self.workspace_id or row["role"] != self.role:
            raise Conflict("caller authorization is revoked or changed")
        return row
    def _event(self, rid: str, action: str, rev: int) -> int:
        doc = {"request_id": rid, "action": action, "revision": rev}
        cur = self.db.execute("INSERT INTO event(workspace_id,request_id,action,revision,event_json) VALUES(?,?,?,?,?)", (self.workspace_id,rid,action,rev,_canonical(doc).decode()))
        return int(cur.lastrowid)
    def _once(self, key: str, operation: str, body: Any, apply: Callable[[],dict[str,Any]], *, drop_after_commit: bool=False) -> dict[str,Any]:
        digest=hashlib.sha256(_canonical(body)).hexdigest(); self.db.execute("BEGIN IMMEDIATE")
        try:
            self._active_identity()
            prior=self.db.execute("SELECT * FROM idem WHERE workspace_id=? AND principal_id=? AND idem_key=?",(self.workspace_id,self.principal_id,key)).fetchone()
            if prior:
                if prior["operation"]!=operation or prior["body_hash"]!=digest: raise Conflict("idempotency key reused for different operation/body")
                result=json.loads(prior["result_json"]); self.db.execute("COMMIT"); return result
            result=apply()
            self.db.execute("INSERT INTO idem VALUES(?,?,?,?,?,?)",(self.workspace_id,self.principal_id,key,operation,digest,_canonical(result).decode()))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction: self.db.execute("ROLLBACK")
            raise
        if drop_after_commit: raise ResponseLost("simulated response lost after durable commit")
        return result
    def _owned_request(self,rid:str)->sqlite3.Row:
        row=self.db.execute("SELECT * FROM request WHERE request_id=? AND workspace_id=?",(rid,self.workspace_id)).fetchone()
        if row is None: raise Conflict("request outside authenticated workspace or missing")
        return row

    def publish(self,rid:str,body:dict[str,Any],dependants:list[str],idem_key:str,*,signed_artifact:dict[str,Any]|None=None,drop_after_commit:bool=False)->dict[str,Any]:
        _reject_identity_claims(body); opbody={"request_id":rid,"body":body,"dependants":sorted(dependants),
            "signed_artifact":None if signed_artifact is None else {
                "artifact_digest":signed_artifact.get("artifact_digest"),"key_id":signed_artifact.get("key_id"),
                "original_bytes":base64.b64encode(signed_artifact.get("original_bytes",b"")).decode("ascii"),
                "envelope_bytes":base64.b64encode(signed_artifact.get("envelope_bytes",b"")).decode("ascii"),
                "signature":base64.b64encode(signed_artifact.get("signature",b"")).decode("ascii")}}
        def apply():
            if self.role not in {"requester","operator","coordinator"}: raise Conflict("role cannot publish")
            if not dependants or len(set(dependants))!=len(dependants): raise Conflict("unique dependent required")
            for dep in dependants:
                link=self.db.execute("SELECT * FROM validated_link WHERE link_id=? AND workspace_id=? AND active=1",(dep,self.workspace_id)).fetchone()
                if link is None or (self.role!="coordinator" and link["owner_principal"]!=self.principal_id): raise Conflict("dependent lacks an active trusted workspace/repo/run owner link")
            self.db.execute("INSERT INTO request(request_id,workspace_id,revision,material_revision,state,body_json) VALUES(?,?,1,1,'open',?)",(rid,self.workspace_id,_canonical(body).decode()))
            self.db.executemany("INSERT INTO dependent(request_id,dependent_id) VALUES(?,?)",[(rid,d) for d in dependants])
            seq=self._event(rid,"published",1); self.db.execute("UPDATE request SET created_seq=? WHERE request_id=?",(seq,rid))
            if signed_artifact is not None:
                self._insert_signed_artifact(rid, 1, body, signed_artifact)
            return {"request_id":rid,"revision":1}
        return self._once(idem_key,"publish",opbody,apply,drop_after_commit=drop_after_commit)

    def _insert_signed_artifact(self,rid:str,material_revision:int,body:dict[str,Any],artifact:dict[str,Any])->None:
        material=material_digest(body)
        try:
            verify_signed_artifact(workspace_id=self.workspace_id,request_id=rid,material_revision=material_revision,
                                   material_digest=material,artifact_digest=artifact["artifact_digest"],
                                   key_id=artifact["key_id"],original_bytes=artifact["original_bytes"],
                                   envelope_bytes=artifact["envelope_bytes"],signature=artifact["signature"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Conflict(f"signed artifact is invalid: {exc}") from exc
        self.db.execute("INSERT INTO signed_artifact VALUES(?,?,?,?,?,?,?,?,?)",
                        (self.workspace_id,rid,material_revision,material,artifact["artifact_digest"],
                         artifact["key_id"],sqlite3.Binary(artifact["original_bytes"]),
                         sqlite3.Binary(artifact["envelope_bytes"]),sqlite3.Binary(artifact["signature"])))

    def revise(self,rid:str,expected:int,body:dict[str,Any],idem_key:str,*,drop_after_commit:bool=False)->dict[str,Any]:
        _reject_identity_claims(body); opbody={"request_id":rid,"expected_revision":expected,"body":body}
        def apply():
            row=self._owned_request(rid)
            if self.role not in {"requester","operator"} or row["revision"]!=expected or row["state"]!="open": raise Conflict("stale revision, request state, or role")
            rev=expected+1; self.db.execute("UPDATE request SET revision=?,material_revision=material_revision+1,body_json=?,reported=0,verified=0,evidence_ref=NULL,artifact_decision_digest=NULL WHERE request_id=?",(rev,_canonical(body).decode(),rid)); self._event(rid,"revised",rev)
            return {"request_id":rid,"revision":rev}
        return self._once(idem_key,"revise",opbody,apply,drop_after_commit=drop_after_commit)

    def withdraw_dependent(self,rid:str,dep_id:str,expected:int,idem_key:str,*,drop_after_commit:bool=False)->dict[str,Any]:
        body={"request_id":rid,"dependent_id":dep_id,"expected_revision":expected}
        def apply():
            row=self._owned_request(rid); dep=self.db.execute("SELECT d.active,l.owner_principal,l.active AS link_active FROM dependent d JOIN validated_link l ON l.link_id=d.dependent_id WHERE d.request_id=? AND d.dependent_id=? AND l.workspace_id=?",(rid,dep_id,self.workspace_id)).fetchone()
            if self.role not in {"requester","operator","coordinator"} or (self.role!="coordinator" and dep is not None and dep["owner_principal"]!=self.principal_id) or row["revision"]!=expected or row["state"]!="open" or dep is None or not dep["active"] or not dep["link_active"]: raise Conflict("stale revision, role, or dependent owner link cannot withdraw")
            rev=expected+1; self.db.execute("UPDATE dependent SET active=0 WHERE request_id=? AND dependent_id=?",(rid,dep_id)); self.db.execute("UPDATE request SET revision=? WHERE request_id=?",(rev,rid)); self._event(rid,"dependent-withdrawn",rev)
            return {"request_id":rid,"revision":rev,"withdrawn_dependent":dep_id}
        return self._once(idem_key,"withdraw-dependent",body,apply,drop_after_commit=drop_after_commit)

    def report_performed(self,rid:str,expected:int,idem_key:str,*,drop_after_commit:bool=False)->dict[str,Any]:
        body={"request_id":rid,"expected_revision":expected}
        def apply():
            row=self._owned_request(rid)
            if row["revision"]!=expected or row["state"]!="open": raise Conflict("stale revision or request not open")
            rev=expected+1; self.db.execute("UPDATE request SET revision=?,reported=1 WHERE request_id=?",(rev,rid)); self._event(rid,"performed-reported",rev)
            return {"request_id":rid,"revision":rev,"reported":True,"verified":False}
        return self._once(idem_key,"report-performed",body,apply,drop_after_commit=drop_after_commit)

    def verify(self,rid:str,expected:int,evidence_ref:str,idem_key:str,*,drop_after_commit:bool=False)->dict[str,Any]:
        if not isinstance(evidence_ref,str) or not re.fullmatch(r"evidence:[A-Za-z0-9._:/-]+",evidence_ref): raise Conflict("typed nonempty evidence reference required")
        body={"request_id":rid,"expected_revision":expected,"evidence_ref":evidence_ref}
        def apply():
            row=self._owned_request(rid)
            if self.role not in {"verifier","operator"}: raise Conflict("role cannot verify outcome")
            if row["revision"]!=expected or not row["reported"] or row["state"]!="open": raise Conflict("verification requires current reported request")
            evidence=self.db.execute("SELECT status FROM evidence WHERE evidence_ref=? AND workspace_id=? AND request_id=? AND material_digest=? AND outcome='performed'",(evidence_ref,self.workspace_id,rid,material_digest(json.loads(row["body_json"])))).fetchone()
            if evidence is None or evidence["status"]!="eligible": raise Conflict("evidence owner has no eligible reference bound to this request, revision meaning, and outcome")
            rev=expected+1; self.db.execute("UPDATE request SET revision=?,verified=1,evidence_ref=? WHERE request_id=?",(rev,evidence_ref,rid)); self._event(rid,"outcome-verified",rev)
            return {"request_id":rid,"revision":rev,"verified":True,"evidence_ref":evidence_ref}
        return self._once(idem_key,"verify",body,apply,drop_after_commit=drop_after_commit)

    def decide_artifact(self,rid:str,expected:int,digest:str,idem_key:str,*,drop_after_commit:bool=False)->dict[str,Any]:
        if not isinstance(digest,str) or not re.fullmatch(r"sha256:[0-9a-f]{64}",digest): raise Conflict("exact sha256 artifact digest required")
        body={"request_id":rid,"expected_revision":expected,"artifact_digest":digest}
        def apply():
            row=self._owned_request(rid)
            if self.role not in {"operator","coordinator"}: raise Conflict("role cannot record an artifact decision")
            if row["revision"]!=expected or row["state"]!="open": raise Conflict("stale revision or request not open")
            body_doc=json.loads(row["body_json"])
            material=material_digest(body_doc)
            material_revision=row["material_revision"]
            artifact=self.db.execute("SELECT * FROM signed_artifact WHERE workspace_id=? AND request_id=? AND material_revision=?",
                                     (self.workspace_id,rid,material_revision)).fetchone()
            if artifact is None or artifact["artifact_digest"]!=digest or artifact["material_digest"]!=material:
                raise Conflict("no signed artifact matches current request material and revision")
            try:
                verify_signed_artifact(workspace_id=self.workspace_id,request_id=rid,material_revision=material_revision,
                                       material_digest=material,artifact_digest=artifact["artifact_digest"],
                                       key_id=artifact["key_id"],original_bytes=artifact["original_bytes"],
                                       envelope_bytes=artifact["envelope_bytes"],signature=artifact["signature"])
            except ValueError as exc:
                raise Conflict(f"stored signed artifact failed verification: {exc}") from exc
            rev=expected+1; self.db.execute("UPDATE request SET revision=?,artifact_decision_digest=? WHERE request_id=?",(rev,digest,rid)); self._event(rid,"artifact-decided",rev)
            return {"request_id":rid,"revision":rev,"artifact_digest":digest}
        return self._once(idem_key,"decide-artifact",body,apply,drop_after_commit=drop_after_commit)

    def resolve(self,rid:str,expected:int,idem_key:str,*,drop_after_commit:bool=False)->dict[str,Any]:
        body={"request_id":rid,"expected_revision":expected}
        def apply():
            row=self._owned_request(rid)
            if self.role not in {"verifier","operator"} or row["revision"]!=expected or not row["verified"] or row["state"]!="open": raise Conflict("resolution requires current verification and verifier/operator role")
            rev=expected+1; self.db.execute("UPDATE request SET revision=?,state='resolved' WHERE request_id=?",(rev,rid)); self._event(rid,"resolved",rev)
            return {"request_id":rid,"revision":rev,"state":"resolved"}
        return self._once(idem_key,"resolve",body,apply,drop_after_commit=drop_after_commit)

    def acknowledge_continuation(self,rid:str,dep_id:str,expected:int,idem_key:str,*,drop_after_commit:bool=False)->dict[str,Any]:
        body={"request_id":rid,"dependent_id":dep_id,"expected_revision":expected}
        def apply():
            row=self._owned_request(rid); dep=self.db.execute("SELECT d.active,d.continuation_ack,l.owner_principal,l.active AS link_active FROM dependent d JOIN validated_link l ON l.link_id=d.dependent_id WHERE d.request_id=? AND d.dependent_id=? AND l.workspace_id=?",(rid,dep_id,self.workspace_id)).fetchone()
            if row["revision"]!=expected or row["state"]!="resolved" or dep is None or not dep["active"] or not dep["link_active"] or dep["continuation_ack"] or (self.role!="coordinator" and dep["owner_principal"]!=self.principal_id): raise Conflict("current resolved request and own active unacknowledged dependent required")
            rev=expected+1; self.db.execute("UPDATE dependent SET continuation_ack=1 WHERE request_id=? AND dependent_id=?",(rid,dep_id)); self.db.execute("UPDATE request SET revision=? WHERE request_id=?",(rev,rid)); self._event(rid,"continuation-acknowledged",rev)
            return {"request_id":rid,"revision":rev,"dependent_id":dep_id,"acknowledged":True}
        return self._once(idem_key,"ack-continuation",body,apply,drop_after_commit=drop_after_commit)

    def _cursor_secret(self)->bytes:
        value=self.db.execute("SELECT value FROM metadata WHERE key='cursor_key'").fetchone()[0]; return base64.urlsafe_b64decode(value.encode())
    def _encode_cursor(self,kind:str,position:int)->str:
        doc={"kind":kind,"workspace_id":self.workspace_id,"principal_id":self.principal_id,"position":position}; raw=_canonical(doc); sig=hmac.new(self._cursor_secret(),raw,hashlib.sha256).digest(); return base64.urlsafe_b64encode(raw+sig).decode()
    def _decode_cursor(self,cursor:str|None,kind:str)->int:
        if cursor is None:return 0
        try:
            rawsig=base64.urlsafe_b64decode(cursor.encode()); raw,sig=rawsig[:-32],rawsig[-32:]
            if not hmac.compare_digest(sig,hmac.new(self._cursor_secret(),raw,hashlib.sha256).digest()):raise ValueError()
            doc=json.loads(raw)
            if doc!={"kind":kind,"workspace_id":self.workspace_id,"principal_id":self.principal_id,"position":doc["position"]}:raise ValueError()
            return int(doc["position"])
        except Exception as exc:raise Conflict("cursor invalid for authenticated principal/workspace") from exc
    def list_requests(self,cursor:str|None=None,*,limit:int=2)->dict[str,Any]:
        self._active_identity()
        if limit<1 or limit>self.max_read:raise Conflict("page size exceeds owner bound")
        position=self._decode_cursor(cursor,"page"); rows=self.db.execute("SELECT request_id,revision,state,created_seq FROM request WHERE workspace_id=? AND created_seq>? ORDER BY created_seq LIMIT ?",(self.workspace_id,position,limit+1)).fetchall(); selected=rows[:limit]
        nxt=self._encode_cursor("page",selected[-1]["created_seq"]) if len(rows)>limit and selected else None
        return {"items":[dict(r) for r in selected],"next_cursor":nxt}
    def watch(self,cursor:str|None=None,*,limit:int=2)->dict[str,Any]:
        self._active_identity()
        if limit<1 or limit>self.max_read:raise Conflict("read size exceeds owner bound")
        position=self._decode_cursor(cursor,"watch"); floor=int(self.db.execute("SELECT value FROM metadata WHERE key=?",(f"retained_from_seq:{self.workspace_id}",)).fetchone()[0])
        if position<floor-1:raise Conflict("watch cursor expired; reset/reconcile required")
        rows=self.db.execute("SELECT seq,event_json FROM event WHERE workspace_id=? AND seq>? ORDER BY seq LIMIT ?",(self.workspace_id,position,limit+1)).fetchall(); selected=rows[:limit]
        nxt=self._encode_cursor("watch",selected[-1]["seq"]) if selected else cursor
        return {"events":[json.loads(r["event_json"]) for r in selected],"next_cursor":nxt,"more":len(rows)>limit}
    def compact_events_through(self,seq:int)->None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._active_identity(); self.db.execute("DELETE FROM event WHERE workspace_id=? AND seq<=?",(self.workspace_id,seq)); key=f"retained_from_seq:{self.workspace_id}"; old=int(self.db.execute("SELECT value FROM metadata WHERE key=?",(key,)).fetchone()[0]); self.db.execute("UPDATE metadata SET value=? WHERE key=?",(str(max(seq+1,old)),key)); self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:self.db.execute("ROLLBACK")
            raise
    def request(self,rid:str)->dict[str,Any]:
        self._active_identity(); return dict(self._owned_request(rid))
    def signed_artifact(self,rid:str,material_revision:int)->dict[str,Any]:
        self._active_identity(); self._owned_request(rid)
        row=self.db.execute("SELECT * FROM signed_artifact WHERE workspace_id=? AND request_id=? AND material_revision=?",
                            (self.workspace_id,rid,material_revision)).fetchone()
        if row is None: raise Conflict("signed artifact is unavailable in this workspace/revision")
        return dict(row)
    def dependants(self,rid:str)->dict[str,dict[str,int]]:
        self._active_identity(); self._owned_request(rid); rows=self.db.execute("SELECT dependent_id,active,continuation_ack FROM dependent WHERE request_id=? ORDER BY dependent_id",(rid,)).fetchall()
        return {r["dependent_id"]:{"active":r["active"],"continuation_ack":r["continuation_ack"]} for r in rows}
    def request_count(self)->int:return self.db.execute("SELECT count(*) FROM request WHERE workspace_id=?",(self.workspace_id,)).fetchone()[0]
