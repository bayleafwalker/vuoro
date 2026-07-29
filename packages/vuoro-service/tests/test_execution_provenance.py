from __future__ import annotations

import pytest

from vuoro_service.execution_provenance import (
    DISPATCH_ENQUEUE_AUTHORITY,
    DispatchEnqueueProvenanceError,
    resolve_dispatch_enqueue_provenance,
)
from vuoro_service.identity import Identity, InvocationContext


def _context(**overrides: object) -> InvocationContext:
    values: dict[str, object] = {
        "identity": Identity(
            actor="agentops:compiler",
            environment="vuoro-shared",
            authorities=frozenset({DISPATCH_ENQUEUE_AUTHORITY}),
            repo_ids=frozenset({"agentops"}),
        ),
        "request_id": "request-2040",
        "basis_revision": "work-revision-7",
        "catalog_revision": "catalog-9",
        "idempotency_requirement": "required",
        "idempotency_key": "enqueue-2040",
        "repo_id": "agentops",
    }
    values.update(overrides)
    return InvocationContext(**values)  # type: ignore[arg-type]


def test_resolves_authenticated_identity_to_narrow_actionq_shaped_provenance() -> None:
    provenance = resolve_dispatch_enqueue_provenance(
        _context(), expected_environment="vuoro-shared"
    )

    assert provenance.actor == "agentops:compiler"
    assert provenance.environment == "vuoro-shared"
    assert provenance.request_id == "request-2040"
    assert provenance.catalog_revision == "catalog-9"
    assert provenance.basis_revision == "work-revision-7"
    assert provenance.idempotency_key == "enqueue-2040"
    assert provenance.authorized_repositories == ("agentops",)


@pytest.mark.parametrize(
    ("context", "message"),
    [
        (object(), "one authenticated"),
        (_context(identity=Identity(actor="", environment="vuoro-shared")), "actor"),
        (_context(repo_id=None), "repository scope"),
        (_context(repo_id="actionq"), "not authorized"),
        (_context(idempotency_key=None), "idempotency key"),
    ],
)
def test_rejects_missing_or_incomplete_authenticated_context(
    context: InvocationContext | object, message: str
) -> None:
    with pytest.raises(DispatchEnqueueProvenanceError, match=message):
        resolve_dispatch_enqueue_provenance(context, expected_environment="vuoro-shared")


def test_rejects_wrong_environment_and_missing_named_authority() -> None:
    with pytest.raises(DispatchEnqueueProvenanceError, match="different environment"):
        resolve_dispatch_enqueue_provenance(
            _context(
                identity=Identity(
                    actor="agentops:compiler",
                    environment="vuoro-dev",
                    authorities=frozenset({DISPATCH_ENQUEUE_AUTHORITY}),
                    repo_ids=frozenset({"agentops"}),
                )
            ),
            expected_environment="vuoro-shared",
        )
    with pytest.raises(DispatchEnqueueProvenanceError, match="execution.enqueue"):
        resolve_dispatch_enqueue_provenance(
            _context(
                identity=Identity(
                    actor="agentops:compiler",
                    environment="vuoro-shared",
                    authorities=frozenset({"execution.read"}),
                    repo_ids=frozenset({"agentops"}),
                )
            ),
            expected_environment="vuoro-shared",
        )


@pytest.mark.parametrize("header", ["Actor", "X-Actor", "X-Caller-Actor"])
def test_rejects_raw_caller_actor_headers(header: str) -> None:
    with pytest.raises(DispatchEnqueueProvenanceError, match="headers are forbidden"):
        resolve_dispatch_enqueue_provenance(
            _context(), expected_environment="vuoro-shared", headers={header: "forged:admin"}
        )
