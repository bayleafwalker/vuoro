"""Structured stale-lease refusal through the published owner's HTTP adapter."""
import uuid

from fastapi.testclient import TestClient
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver

from .sprintctl_binding import SprintctlProvider


@pytest.mark.essential_safety
def test_pinned_owner_stale_outcome_preserves_generation_details_over_http(request):
    url = request.config.getoption("--lease-pg-url")
    if url is None:
        pytest.skip("requires the configured disposable PostgreSQL owner binding")
    from sprintctl.vuoro_adapter import register_work_catalog

    owner = SprintctlProvider(url)
    try:
        item = owner.new_subject()
        first = owner.claim(item, "A")
        owner.make_stale(first)  # Only the declared disposable-database time hook.
        second = owner.claim(item, "B")
        registry = CatalogRegistry()
        register_work_catalog(registry, owner.app)
        identity = Identity(
            actor="A", environment="lease-conformance",
            authorities=frozenset({"work:read", "work:claim", "work:evidence"}),
            principal_id="github:100:0", workspace_id="lease-conformance",
            repo_ids=frozenset({owner.store.repo_id}),
        )
        with TestClient(create_app(
            settings=ServiceSettings(environment_name="lease-conformance"),
            registry=registry,
            identity_resolver=StaticBearerIdentityResolver({"caller-a": identity}),
        )) as client:
            response = client.post(
                "/api/invoke/v1",
                headers={"Authorization": "Bearer caller-a", "X-Vuoro-Client-Protocol": "1"},
                json={
                    "schema_version": "invocation/v1", "request_id": uuid.uuid4().hex,
                    "repo_id": owner.store.repo_id, "catalog_revision": registry.revision,
                    "operation": "work.lease.report-outcome-v1",
                    "arguments": {
                        "lease_id": first.claim_id, "run_id": owner.run("A"),
                        "outcome": "succeeded", "summary": "Stale non-deciding result",
                        "payload": {"result": "old"},
                        "checks": [{"name": "tests", "status": "passed"}],
                        "idempotency_key": uuid.uuid4().hex,
                    },
                },
            )
        assert response.status_code == 409, response.text
        envelope = response.json()
        assert envelope["status"] == "rejected"
        assert envelope["error"]["code"] == "claim-superseded"
        assert envelope["error"]["details"] == {
            "claim_id": first.claim_id, "current_generation": 2, "reported_generation": 1,
        }
        assert owner.current_claim_id(item) == second.claim_id
        assert owner.retained_outcomes(item) == [{
            "claim_id": first.claim_id, "outcome": {"result": "old"},
            "disposition": "stale", "settlement_effect": "none",
        }]
    finally:
        owner.close()
