"""Binding to immutable pinned sprintctl served work.lease.* operations.

Only setup and the explicitly declared time hook use the disposable DB.
All lease observations and transitions use WorkApplication.postgres.invoke.
"""
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid
from .lease_contract import Claim, Outcome, Refused

_CODES = {"lease-held": "LEASE_HELD", "claim-superseded": "CLAIM_SUPERSEDED",
          "lease-expired": "CLAIM_SUPERSEDED", "lease-ended": "CLAIM_SUPERSEDED", "lease-not-found": "CLAIM_SUPERSEDED",
          "idempotency-conflict": "IDEMPOTENCY_KEY_REUSED"}


class SprintctlProvider:
    def __init__(self, url):
        parsed = urlsplit(url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("lease conformance requires a loopback disposable PostgreSQL database")
        if not (parsed.path.startswith("/lease_conformance") or parsed.path == "/m14_scenario"):
            raise ValueError("database must be lease_conformance* or the CI disposable m14_scenario")
        from sprintctl import pg
        from sprintctl.application import WorkApplication, ApplicationRejection
        self.pg = pg
        self.rejection = ApplicationRejection
        self.store = pg.get_connection(url)
        self.store.repo_id = "lease-conformance-" + uuid.uuid4().hex
        pg.init_db(self.store)
        self.app = WorkApplication.postgres(self.store)
        sprint = pg.create_sprint(self.store, "Lease conformance", "Contract", "2026-01-01", "2026-12-31", "active")
        self.track = pg.get_or_create_track(self.store, sprint, "contract")
        self.sprint = sprint
        self.runs = {}
        self.claim_keys = {}

    def close(self):
        self.store.conn.close()

    def context(self, holder):
        return SimpleNamespace(identity=SimpleNamespace(actor=holder, environment="lease-conformance",
            authorities=frozenset({"work:evidence", "work:claim", "work:read"}),
            principal_id="github:" + str(100 if holder == "A" else 200) + ":0",
            workspace_id="lease-conformance", client_id=None, grant_id=None),
            request_id="req-" + uuid.uuid4().hex, basis_revision=None,
            catalog_revision="contract", idempotency_requirement="not-allowed", idempotency_key=None)

    def invoke(self, operation, arguments, holder="A"):
        try:
            return self.app.invoke(operation, arguments, self.context(holder))
        except self.rejection as error:
            # Unknown owner codes fail the binding rather than becoming generic refusals.
            raise Refused(_CODES[error.code]) from error

    def new_subject(self):
        return str(self.pg.create_work_item(self.store, self.sprint, self.track, "Lease contract"))

    def run(self, holder):
        if holder not in self.runs:
            self.runs[holder] = self.invoke("work.run.register-v1", {
                "harness_id": "conformance", "harness_build": "test", "model_id": "scripted",
                "recipe_id": "lease-contract/v1", "observed_profile": {
                    "instruction_digest": "sha256:" + "a" * 64, "skill_digests": []},
                "idempotency_key": uuid.uuid4().hex}, holder)["run"]["run_id"]
        return self.runs[holder]

    def claim(self, subject, holder):
        row = self.invoke("work.lease.acquire-v1", {"item_id": int(subject),
            "run_id": self.run(holder), "idempotency_key": self.claim_keys.setdefault(
                (subject, holder), uuid.uuid4().hex)}, holder)["lease"]
        return Claim(row["lease_id"], subject, holder)

    def heartbeat(self, handle, holder):
        self.invoke("work.lease.heartbeat-v1", {"lease_id": handle.claim_id,
                    "run_id": self.run(holder)}, holder)

    def report_outcome(self, handle, holder, outcome):
        try:
            response = self.invoke("work.lease.report-outcome-v1", {"lease_id": handle.claim_id,
                "run_id": self.run(holder), "outcome": "succeeded", "summary": "Contract result",
                "payload": outcome, "checks": [{"name": "tests", "status": "passed"}],
                "idempotency_key": uuid.uuid4().hex}, holder)
        except Refused as error:
            return Outcome(False, error.code)
        return Outcome(response["settled"])

    def make_stale(self, handle):
        with self.store.conn.cursor() as cursor:
            cursor.execute("UPDATE work_lease SET heartbeat_at = heartbeat_at - (ttl_seconds + 1) * interval '1 second' WHERE repo_id = %s AND lease_id = %s", (self.store.repo_id, handle.claim_id))
        self.store.conn.commit()

    def read(self, subject):
        return self.invoke("work.lease.read-v1", {"item_id": int(subject)})

    def retained_outcomes(self, subject):
        retained = []
        for report in self.read(subject)["outcome_reports"]:
            if report["disposition"] != "rejected":
                continue
            # Normalize the owner's refused, non-deciding record into INV-L1.
            # A decision-bearing or unrelated rejected report must fail this oracle.
            assert report["decision_id"] is None
            assert _CODES[report["reason_code"]] == "CLAIM_SUPERSEDED"
            retained.append(dict(claim_id=report["lease_id"], outcome=report["payload"],
                                 disposition="stale", settlement_effect="none"))
        return retained

    def current_claim_id(self, subject):
        row = self.read(subject)["current_lease"]
        return row["lease_id"] if row else None
