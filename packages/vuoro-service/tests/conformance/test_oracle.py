"""Force INV-L1's oracle to fail against a deliberately broken provider."""
import pytest
from vuoro_service.lease import LeaseStore
from .reference import ReferenceProvider
from .test_lease_contract import assert_late_report_is_nonsettling_and_retained


class SettlesSupersededStore(LeaseStore):
    def complete(self, lease_id, holder, *, result=None):
        if self._current_for_lease_id(lease_id) is None:
            return
        return super().complete(lease_id, holder, result=result)


def test_oracle_rejects_settlement_of_a_superseded_report():
    with pytest.raises(AssertionError):
        assert_late_report_is_nonsettling_and_retained(ReferenceProvider(SettlesSupersededStore))
