"""Neutral API bindings; no observation of provider storage layouts.

Reference uses existing chain writer and idempotency primitives, in tests only.
Published-owner binding uses native run/evidence operations. Fixture setup is
shared with existing effect conformance; reconnect makes a fresh PG connection.
"""
import asyncio
from copy import deepcopy
from datetime import datetime

from authority_pg_binding import PgEffectsBinding
from vuoro_evidence.core.chain import entry_digest
from vuoro_evidence.core.model import Claim, ClaimType, EvidenceItem, ValidityBasis, ValidityWindow
from vuoro_evidence.core.set_builder import EvidenceSetBuilder
from vuoro_mcp_edge.idempotency import InMemoryIdempotencyLedger, StoredResult, replay_or_conflict, request_digest


def model(item):
    validity = item["validity"]
    return EvidenceItem(item_id=item["item_id"], kind=item["kind"], ref=item["ref"],
        digest=item["digest"], collector=item["collector"],
        validity=ValidityWindow(ValidityBasis(validity["basis"]),
            datetime.fromisoformat(validity["valid_from"])),
        claims=tuple(Claim(ClaimType(c["claim_type"]), c["subject"],
            grant_id=c["grant_id"], confirms=c["confirms"], detail=c["detail"]) for c in item["claims"]),
        provenance=item["provenance"], chain_seq=item.get("chain_seq"),
        chain_prev_digest=item.get("chain_prev_digest"))


class ReferenceProviderIngestion:
    def __init__(self):
        self.ledger = InMemoryIdempotencyLedger()
        self.builder = EvidenceSetBuilder("provider-contract")
        self.receipts = []
        self.binding = {"repo_id": "reference-repo", "run_id": "reference-run",
            "principal_id": "reference:producer:0", "workspace_id": "workspace",
            "client_id": None, "grant_id": None}

    def append(self, draft):
        digest = request_digest("append_evidence", {**draft, "run_id": self.binding["run_id"]})
        scope = (self.binding["workspace_id"], self.binding["principal_id"],
                 "append_evidence", draft["idempotency_key"])
        existing = asyncio.run(self.ledger.lookup(*scope))
        if existing is not None:
            return deepcopy(replay_or_conflict(existing, digest))
        linked = self.builder.add(model(draft))
        item = {k: deepcopy(v) for k, v in draft.items() if k != "idempotency_key"}
        item.update(chain_seq=linked.chain_seq, chain_prev_digest=linked.chain_prev_digest)
        self.receipts.append(item)
        stored = asyncio.run(self.ledger.store(*scope, StoredResult(digest, item)))
        return deepcopy(replay_or_conflict(stored, digest))

    def tail(self):
        return deepcopy(self.receipts[-1]) if self.receipts else None

    def resolve(self):
        return deepcopy(self.binding)

    def item_status(self):
        return "pending"  # Reference has no work-settlement operation.

    def reconnect(self):
        pass  # Only a reference facade; PG below tests a real new connection.

    def close(self):
        pass


class PgProviderIngestion:
    def __init__(self, url):
        self.url = url
        self.owner = PgEffectsBinding(url)

    def invoke(self, operation, arguments):
        return self.owner.app.invoke(operation, arguments,
            self.owner.context("proposer", {"work:evidence", "work:read"}))

    def append(self, draft):
        tail = self.tail()
        args = {**deepcopy(draft), "run_id": self.owner.run,
            "chain_seq": 0 if tail is None else tail["chain_seq"] + 1,
            "chain_prev_digest": None if tail is None else entry_digest(model(tail))}
        return self.invoke("work.evidence.append-v1", args)["item"]

    def tail(self):
        return self.invoke("work.evidence.tail-v1", {"run_id": self.owner.run})["item"]

    def resolve(self):
        return self.invoke("work.run.resolve-v1", {"run_id": self.owner.run})

    def item_status(self):
        return self.owner.item_status()

    def reconnect(self):
        from sprintctl import pg
        from sprintctl.application import WorkApplication
        repo_id = self.owner.owner_store.repo_id
        self.owner.owner_store.conn.close()
        self.owner.owner_store = pg.get_connection(self.url)
        self.owner.owner_store.repo_id = repo_id
        self.owner.app = WorkApplication.postgres(self.owner.owner_store)

    def close(self):
        self.owner.close()
