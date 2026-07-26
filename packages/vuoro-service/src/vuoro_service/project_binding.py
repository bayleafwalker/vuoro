"""Immutable project-composition inputs and served authorization checks.

Project bindings originate in their home repository; a deployed Vuoro image
must consume an immutable, release-reviewed projection of that binding rather
than discovering a caller's ``project.toml``.  This module deliberately has no
filesystem or environment access.  Composition supplies the already-validated
input and the work adapter supplies the project application.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Callable, Protocol
from uuid import UUID


_REPO_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class ProjectBindingError(ValueError):
    """An immutable project-composition input is malformed or unsafe."""


class ProjectAuthorizationError(PermissionError):
    """The caller cannot safely receive a complete project aggregate."""


@dataclass(frozen=True, slots=True)
class ProjectMember:
    """One ordered backlog member in a release-reviewed project projection."""

    repo_id: str


@dataclass(frozen=True, slots=True)
class ProjectBinding:
    """Minimal canonical project data required by a served aggregate.

    ``source_revision`` and ``source_sha256`` identify the exact canonical
    ``project.toml`` from which the projection was produced.  They make the
    copied release input auditable without making Vuoro the project authority.
    """

    project_id: str
    home_repo: str
    members: tuple[ProjectMember, ...]
    source_repository: str
    source_revision: str
    source_path: str
    source_sha256: str

    @property
    def repo_ids(self) -> tuple[str, ...]:
        return tuple(member.repo_id for member in self.members)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProjectBinding":
        expected = {
            "project_id",
            "home_repo",
            "members",
            "source_repository",
            "source_revision",
            "source_path",
            "source_sha256",
        }
        if set(raw) != expected:
            raise ProjectBindingError("project binding fields do not match the v1 contract")
        project_id = _uuid4(raw["project_id"], "project_id")
        home_repo = _repo_id(raw["home_repo"], "home_repo")
        source_repository = _https_url(raw["source_repository"], "source_repository")
        source_revision = _sha(raw["source_revision"], "source_revision", 40)
        source_path = _source_path(raw["source_path"])
        source_sha256 = _sha(raw["source_sha256"], "source_sha256", 64)
        raw_members = raw["members"]
        if not isinstance(raw_members, list) or not raw_members:
            raise ProjectBindingError("members must be a non-empty array")
        members: list[ProjectMember] = []
        for index, member in enumerate(raw_members):
            if not isinstance(member, dict) or set(member) != {"repo_id"}:
                raise ProjectBindingError(f"members[{index}] must contain only repo_id")
            members.append(ProjectMember(_repo_id(member["repo_id"], f"members[{index}].repo_id")))
        repo_ids = [member.repo_id for member in members]
        if len(repo_ids) != len(set(repo_ids)):
            raise ProjectBindingError("member repo_id values must be unique")
        if home_repo not in repo_ids:
            raise ProjectBindingError("home_repo must appear in members")
        return cls(
            project_id=project_id,
            home_repo=home_repo,
            members=tuple(members),
            source_repository=source_repository,
            source_revision=source_revision,
            source_path=source_path,
            source_sha256=source_sha256,
        )


def load_project_bindings(raw: Mapping[str, Any]) -> tuple[ProjectBinding, ...]:
    """Parse a deterministic ``vuoro-project-bindings/v1`` input.

    The caller is responsible for obtaining this mapping from the immutable
    service composition.  Empty bindings are valid: project operations remain
    unavailable until a reviewed binding is added to a future release.
    """

    if set(raw) != {"schema_version", "bindings"}:
        raise ProjectBindingError("project bindings must use the v1 top-level shape")
    if raw["schema_version"] != "vuoro-project-bindings/v1":
        raise ProjectBindingError("unsupported project bindings manifest")
    items = raw["bindings"]
    if not isinstance(items, list):
        raise ProjectBindingError("project bindings must be an array")
    bindings = tuple(
        ProjectBinding.from_dict(item) for item in items if isinstance(item, dict)
    )
    if len(bindings) != len(items):
        raise ProjectBindingError("each project binding must be an object")
    if len({binding.project_id for binding in bindings}) != len(bindings):
        raise ProjectBindingError("project_id values must be unique")
    return bindings


class _Identity(Protocol):
    def authorizes_repo(self, repo_id: str) -> bool: ...


class _InvocationContext(Protocol):
    identity: _Identity


class ProjectApplication(Protocol):
    def invoke(
        self, operation: str, arguments: Mapping[str, Any], context: Any
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AuthorizedProjectApplication:
    """Fail closed unless a project reader is authorized for every member.

    Project operations intentionally have no envelope ``repo_id`` because a
    result can span several repositories.  Consequently the normal service
    repo gate cannot establish this invariant.  The composed project handler
    checks every bound member *before* it invokes the owner application, so it
    cannot leak a partial aggregate or cause a member read for an unauthorized
    identity.
    """

    binding: ProjectBinding
    application: ProjectApplication

    def invoke(
        self, operation: str, arguments: Mapping[str, Any], context: _InvocationContext
    ) -> dict[str, Any]:
        identity = getattr(context, "identity", None)
        authorizes_repo = getattr(identity, "authorizes_repo", None)
        if not callable(authorizes_repo) or not all(
            authorizes_repo(repo_id) for repo_id in self.binding.repo_ids
        ):
            raise ProjectAuthorizationError(
                "identity must be authorized for every repository in the project"
            )
        return self.application.invoke(operation, arguments, context)


def compose_authorized_project_application(
    binding: ProjectBinding,
    *,
    make_member_application: Callable[[str], Any],
    make_project_application: Callable[[str, tuple[tuple[str, Any], ...]], ProjectApplication],
) -> AuthorizedProjectApplication:
    """Construct one domain application per immutable project member.

    Composition supplies a factory which creates a freshly repo-scoped
    ``WorkApplication`` for each member.  Keeping this mechanical fan-out in
    the service shell prevents an aggregate from accidentally delegating to a
    single request-rescoping application.
    """

    members = tuple(
        (member.repo_id, make_member_application(member.repo_id))
        for member in binding.members
    )
    return AuthorizedProjectApplication(
        binding=binding,
        application=make_project_application(binding.project_id, members),
    )


def _repo_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _REPO_ID.fullmatch(value):
        raise ProjectBindingError(f"{field} must be a valid repo_id")
    return value


def _uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ProjectBindingError(f"{field} must be a canonical UUIDv4")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ProjectBindingError(f"{field} must be a canonical UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ProjectBindingError(f"{field} must be a canonical UUIDv4")
    return value


def _sha(value: Any, field: str, length: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise ProjectBindingError(f"{field} must be a lowercase SHA-{length * 4} digest")
    return value


def _https_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ProjectBindingError(f"{field} must be an HTTPS URL")
    return value


def _source_path(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value.split("/"):
        raise ProjectBindingError("source_path must be a relative path without parent traversal")
    return value


__all__ = [
    "AuthorizedProjectApplication",
    "ProjectBinding",
    "ProjectBindingError",
    "ProjectAuthorizationError",
    "ProjectMember",
    "compose_authorized_project_application",
    "load_project_bindings",
]
