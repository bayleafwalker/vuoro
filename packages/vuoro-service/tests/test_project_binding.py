from __future__ import annotations

from dataclasses import dataclass

import pytest

from vuoro_service.project_binding import (
    AuthorizedProjectApplication,
    ProjectBindingError,
    ProjectAuthorizationError,
    compose_authorized_project_application,
    load_project_bindings,
)


def _manifest(*, members: list[str] | None = None) -> dict:
    return {
        "schema_version": "vuoro-project-bindings/v1",
        "bindings": [
            {
                "project_id": "981b2073-d7af-4c28-bff3-3cf807495fba",
                "home_repo": "agentops",
                "members": [{"repo_id": value} for value in (members or ["agentops", "vuoro"])],
                "source_repository": "https://github.com/bayleafwalker/agentops",
                "source_revision": "a" * 40,
                "source_path": "project.toml",
                "source_sha256": "b" * 64,
            }
        ],
    }


def test_project_binding_is_strict_and_keeps_declared_member_order() -> None:
    binding = load_project_bindings(_manifest())[0]

    assert binding.project_id == "981b2073-d7af-4c28-bff3-3cf807495fba"
    assert binding.repo_ids == ("agentops", "vuoro")
    assert binding.source_path == "project.toml"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["bindings"][0].update(source_path="../project.toml"), "source_path"),
        (lambda raw: raw["bindings"][0].update(members=[{"repo_id": "agentops"}, {"repo_id": "agentops"}]), "unique"),
        (lambda raw: raw["bindings"][0].update(home_repo="kctl"), "home_repo"),
        (lambda raw: raw.update(schema_version="project/v2"), "unsupported"),
    ],
)
def test_project_binding_rejects_ambiguous_or_noncanonical_inputs(mutate, message: str) -> None:
    raw = _manifest()
    mutate(raw)

    with pytest.raises(ProjectBindingError, match=message):
        load_project_bindings(raw)


@dataclass
class _Identity:
    allowed: frozenset[str]

    def authorizes_repo(self, repo_id: str) -> bool:
        return repo_id in self.allowed


@dataclass
class _Context:
    identity: _Identity


class _Application:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, operation: str, arguments: dict, context: _Context) -> dict:
        self.calls += 1
        return {"operation": operation, "repositories": ["agentops", "vuoro"]}


def test_project_read_requires_authorization_for_every_returned_member() -> None:
    binding = load_project_bindings(_manifest())[0]
    application = _Application()
    guarded = AuthorizedProjectApplication(binding, application)

    accepted = guarded.invoke(
        "work.project.context", {}, _Context(_Identity(frozenset({"agentops", "vuoro"})))
    )
    assert accepted["repositories"] == ["agentops", "vuoro"]
    assert application.calls == 1

    with pytest.raises(ProjectAuthorizationError, match="every repository"):
        guarded.invoke(
            "work.project.context", {}, _Context(_Identity(frozenset({"agentops"})))
        )
    assert application.calls == 1


def test_composition_constructs_a_distinct_member_application_for_every_repo() -> None:
    binding = load_project_bindings(_manifest())[0]
    constructed: list[str] = []
    received: list[tuple[str, object]] = []
    application = _Application()

    guarded = compose_authorized_project_application(
        binding,
        make_member_application=lambda repo_id: constructed.append(repo_id) or object(),
        make_project_application=lambda project_id, members: received.extend(members) or application,
    )

    assert constructed == ["agentops", "vuoro"]
    assert [repo_id for repo_id, _application in received] == ["agentops", "vuoro"]
    assert guarded.binding is binding
