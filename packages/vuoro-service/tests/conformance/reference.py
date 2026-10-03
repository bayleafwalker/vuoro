"""Binding to the always-run in-memory reference behaviour spec."""
import uuid
from vuoro_service.lease import LeaseStore, LeaseConflictError, LeaseNotCurrentError
from .lease_contract import Claim, Outcome, Refused


class ReferenceProvider:
    def __init__(self, store_type=LeaseStore):
        self.now = 1000.0
        self.store = store_type(clock=lambda: self.now)
        self.current = {}

    def new_subject(self):
        return str(uuid.uuid4())

    def claim(self, subject, holder):
        try:
            lease = self.store.claim(subject, holder, ttl_seconds=10)
        except LeaseConflictError as error:
            raise Refused("LEASE_HELD") from error
        self.current[subject] = lease.lease_id
        return Claim(lease.lease_id, subject, holder)

    def heartbeat(self, handle, holder):
        try:
            self.store.heartbeat(handle.claim_id, holder)
        except LeaseNotCurrentError as error:
            raise Refused("CLAIM_SUPERSEDED") from error

    def report_outcome(self, handle, holder, outcome):
        try:
            self.store.complete(handle.claim_id, holder, result=outcome)
        except LeaseNotCurrentError:
            return Outcome(False, "CLAIM_SUPERSEDED")
        self.current.pop(handle.subject, None)
        return Outcome(True)

    def make_stale(self, handle):
        self.now += 11

    def retained_outcomes(self, subject):
        return [dict(claim_id=o.lease_id, outcome=o.result,
                     disposition=o.disposition, settlement_effect=o.settlement_effect)
                for o in self.store.retained_outcomes(subject)]

    def current_claim_id(self, subject):
        lease = self.store._current(subject)
        return lease.lease_id if lease else None
