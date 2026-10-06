"""Portable provider evidence replay contract; synthetic events, actual owners.

No test consults owner table names or counts. Receipt identity, chain position,
run binding and ordinary work status are observed through binding operations.
"""
import os
from datetime import datetime, timezone

import pytest

from provider_ingestion_binding import ReferenceProviderIngestion, PgProviderIngestion
from vuoro_evidence.ingress.provider import normalize, evidence_item_draft

PROVIDERS = ["reference"] + (["sprintctl-pg"] if os.environ.get("VUORO_AUTHORITY_TEST_PG_URL") else [])
FAMILIES = ["check_run", "workflow_run", "pull_request", "claude-outcome"]


@pytest.fixture(params=PROVIDERS)
def ingestion(request):
    binding = (PgProviderIngestion(os.environ["VUORO_AUTHORITY_TEST_PG_URL"])
               if request.param == "sprintctl-pg" else ReferenceProviderIngestion())
    try:
        yield binding
    finally:
        binding.close()


def draft(family, *, delivery="delivery-1", artifact="b"):
    if family == "claude-outcome":
        provider, event = "anthropic-managed-agents", "span.outcome_evaluation_end"
        payload = {"type": event, "id": "event-1", "result": "satisfied", "outcome_id": "outcome-1"}
    else:
        provider, event = "github", family
        value = {"id": 1, "head_sha": "a" * 40, "conclusion": "success"}
        if family == "pull_request":
            value = {"id": 1, "head": {"sha": "a" * 40}}
        payload = {"action": "completed" if family != "pull_request" else "opened",
                   "repository": {"full_name": "owner/repo"}, event: value}
    observation = normalize(provider, event, payload, delivery_id=delivery,
                            observed={"artifact_digest": "sha256:" + artifact * 64})
    return evidence_item_draft(observation, ref="fixture:provider-payload",
                              collected_at=datetime(2026, 10, 6, tzinfo=timezone.utc))


def lost_reply(binding, request):
    binding.append(request)
    raise OSError("reply lost after committed append")


@pytest.mark.parametrize("family", FAMILIES)
def test_committed_provider_reply_loss_reconnect_and_exact_retry(ingestion, family):
    request = draft(family)
    binding = ingestion.resolve()
    assert ingestion.tail() is None
    with pytest.raises(OSError, match="after committed"):
        lost_reply(ingestion, request)
    committed = ingestion.tail()
    assert committed["chain_seq"] == 0
    ingestion.reconnect()
    assert ingestion.resolve() == binding
    assert ingestion.tail() == committed
    assert ingestion.append(request) == committed
    assert ingestion.tail() == committed
    assert ingestion.item_status() == "pending"
    observation = committed["claims"][0]
    assert observation["confirms"] is None and observation["grant_id"] is None
    assert observation["detail"]["assurance"] == "unverified-supplied-payload"
    for field in ("provider_build", "session_reference", "rubric_or_check_revision", "observed_instruction_digest"):
        assert observation["detail"][field] == "unknown"


@pytest.mark.parametrize("family", FAMILIES)
def test_same_provider_delivery_changed_artifact_refuses_without_history_or_identity_change(ingestion, family):
    request = draft(family)
    first = ingestion.append(request)
    before_binding = ingestion.resolve()
    changed = draft(family, artifact="c")
    assert changed["idempotency_key"] == request["idempotency_key"]
    assert changed["digest"] != request["digest"]
    with pytest.raises(Exception) as error:
        ingestion.append(changed)
    assert getattr(error.value, "code", None) == "idempotency-conflict"
    assert ingestion.tail() == first
    assert ingestion.resolve() == before_binding
    assert ingestion.append(request) == first
    assert ingestion.item_status() == "pending"


@pytest.mark.parametrize("family", FAMILIES)
def test_distinct_provider_delivery_extends_chain_and_old_retry_preserves_new_tail(ingestion, family):
    first_request = draft(family)
    first = ingestion.append(first_request)
    second = ingestion.append(draft(family, delivery="delivery-2"))
    assert second["item_id"] != first["item_id"]
    assert second["chain_seq"] == 1 and second["chain_prev_digest"] is not None
    assert ingestion.append(first_request) == first
    assert ingestion.tail() == second
    assert ingestion.item_status() == "pending"


def test_reply_loss_oracle_detects_duplicate_append_mutant():
    class DuplicatingReplay(ReferenceProviderIngestion):
        def append(self, request):
            self.ledger = type(self.ledger)()
            return super().append(request)
    with pytest.raises(AssertionError):
        test_committed_provider_reply_loss_reconnect_and_exact_retry(DuplicatingReplay(), "check_run")


def test_conflict_oracle_detects_missing_delivery_binding_mutant():
    class ForgettingFirstBinding(ReferenceProviderIngestion):
        def append(self, request):
            self.ledger = type(self.ledger)()
            return super().append(request)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        test_same_provider_delivery_changed_artifact_refuses_without_history_or_identity_change(
            ForgettingFirstBinding(), "check_run")
