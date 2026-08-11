"""Immutable project-composition inputs and served authorization checks.

Project bindings originate in their home repository or the Vuoro Cloud
deployment contract; Vuoro must consume a reviewed canonical projection or the
strictly-shaped Cloud document rather than discovering a caller's
``project.toml``.  This module deliberately has no filesystem or environment
access.  Composition supplies the already-validated input and the work adapter
supplies the project application.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Callable, Protocol
from uuid import UUID


_REPO_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class ProjectBindingError(ValueError):
    """An immutable project-composition input is malformed or unsafe."""


class ProjectAuthorizationError(PermissionError):
    """The caller cannot safely receive a complete project aggregate."""


@dataclass(frozen=True, slots=True)
class ProjectMember:
    """One ordered backlog member and its optional hosted provenance."""

    repo_id: str
    git_remote: str | None = None
    commit_sha: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectBinding:
    """Minimal project data required by a served aggregate.

    For the checked-in form, ``source_revision`` and ``source_sha256`` identify
    the exact canonical ``project.toml`` from which the projection was
    produced.  For the Cloud form, ``environment``, ``descriptor_digest`` and
    member repository metadata preserve Cloud's release provenance without
    making Vuoro the project authority.
    """

    project_id: str
    home_repo: str | None
    members: tuple[ProjectMember, ...]
    source_repository: str | None
    source_revision: str | None
    source_path: str | None
    source_sha256: str
    environment: str | None = None
    descriptor_digest: str | None = None

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
        project_id = _project_id(raw["project_id"], "project_id")
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

    @classmethod
    def from_hosted_dict(
        cls, raw: Mapping[str, Any], *, environment: str
    ) -> "ProjectBinding":
        expected = {"project_id", "descriptor_digest", "repositories"}
        if set(raw) != expected:
            raise ProjectBindingError("hosted project fields do not match the v1 contract")
        project_id = _project_id(raw["project_id"], "projects[].project_id")
        descriptor_digest = _descriptor_digest(raw["descriptor_digest"])
        raw_repositories = raw["repositories"]
        if not isinstance(raw_repositories, list) or not raw_repositories:
            raise ProjectBindingError("projects[].repositories must be a non-empty array")
        members: list[ProjectMember] = []
        for index, repository in enumerate(raw_repositories):
            if not isinstance(repository, dict) or set(repository) != {
                "repo_id", "git_remote", "commit_sha"
            }:
                raise ProjectBindingError(
                    f"projects[].repositories[{index}] fields do not match the v1 contract"
                )
            git_remote = repository["git_remote"]
            if git_remote is not None and (
                not isinstance(git_remote, str) or not git_remote.strip()
            ):
                raise ProjectBindingError("repository git_remote must be null or non-empty")
            commit_sha = repository["commit_sha"]
            if commit_sha is not None and (
                not isinstance(commit_sha, str)
                or not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha)
            ):
                raise ProjectBindingError(
                    "repository commit_sha must be null or a lowercase Git SHA"
                )
            members.append(
                ProjectMember(
                    _repo_id(repository["repo_id"], f"repositories[{index}].repo_id"),
                    git_remote=git_remote,
                    commit_sha=commit_sha,
                )
            )
        repo_ids = [member.repo_id for member in members]
        if len(repo_ids) != len(set(repo_ids)):
            raise ProjectBindingError("member repo_id values must be unique")
        return cls(
            project_id=project_id,
            home_repo=None,
            members=tuple(members),
            source_repository=None,
            source_revision=None,
            source_path=None,
            source_sha256=descriptor_digest.removeprefix("sha256:"),
            environment=environment,
            descriptor_digest=descriptor_digest,
        )


def load_project_bindings(raw: Mapping[str, Any]) -> tuple[ProjectBinding, ...]:
    """Parse a deterministic ``vuoro-project-bindings/v1`` input.

    The caller is responsible for obtaining this mapping from the checked-in
    service composition or the approved Cloud runtime mount.  Empty bindings
    are syntactically valid here; composition rejects them before startup
    because a served release requires exactly one project.
    """

    if not isinstance(raw, dict) or raw.get("schema_version") != "vuoro-project-bindings/v1":
        raise ProjectBindingError("unsupported project bindings manifest")
    if set(raw) == {"schema_version", "environment", "projects"}:
        environment = raw["environment"]
        if not isinstance(environment, str) or not environment or environment != environment.strip():
            raise ProjectBindingError("environment must be a non-empty string")
        projects = raw["projects"]
        if not isinstance(projects, list):
            raise ProjectBindingError("projects must be an array")
        bindings = tuple(
            ProjectBinding.from_hosted_dict(item, environment=environment)
            for item in projects
            if isinstance(item, dict)
        )
        if len(bindings) != len(projects):
            raise ProjectBindingError("each hosted project must be an object")
        if len({binding.project_id for binding in bindings}) != len(bindings):
            raise ProjectBindingError("project_id values must be unique")
        return bindings
    if set(raw) != {"schema_version", "bindings"}:
        raise ProjectBindingError("project bindings must use the v1 top-level shape")
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


def _project_id(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ProjectBindingError(f"{field} must be a canonical UUIDv4 or hosted ULID")
    try:
        parsed = UUID(value)
    except ValueError:
        if _ULID.fullmatch(value):
            return value
        raise ProjectBindingError(f"{field} must be a canonical UUIDv4 or hosted ULID")
    if parsed.version != 4 or str(parsed) != value:
        raise ProjectBindingError(f"{field} must be a canonical UUIDv4 or hosted ULID")
    return value


def _descriptor_digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ProjectBindingError("descriptor_digest must be a sha256 digest")
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
