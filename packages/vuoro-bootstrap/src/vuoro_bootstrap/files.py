"""Fail-closed local file planning for an exchanged bootstrap session."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from urllib.parse import urlsplit


class BootstrapFilesError(ValueError):
    """Bootstrap output cannot be rendered without an unsafe overwrite."""


_IMMUTABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?$")


@dataclass(frozen=True)
class BootstrapFilePlan:
    files: dict[Path, bytes]
    commands: tuple[str, ...]


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _assert_no_symlink(path: Path) -> None:
    """Reject every existing symlink in an output path before writing."""

    current = path.absolute()
    while current != current.parent:
        if current.is_symlink():
            raise BootstrapFilesError(
                f"refusing to follow symlink in bootstrap output: {current}"
            )
        current = current.parent


def _assert_https_endpoint(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise BootstrapFilesError(f"{label} must be an HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise BootstrapFilesError(
            f"{label} must be an HTTPS URL without credentials or query data"
        )
    return value.rstrip("/")


def _open_parent(path: Path) -> tuple[int, str]:
    absolute = path.absolute()
    if any(part in {".", ".."} for part in absolute.parts):
        raise BootstrapFilesError(f"bootstrap output path is not canonical: {path}")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor, absolute.name
    except Exception:
        os.close(descriptor)
        raise


def _open_existing_parent(path: Path) -> tuple[int, str]:
    """Open an existing parent directory without traversing symlinks."""

    absolute = path.absolute()
    if any(part in {".", ".."} for part in absolute.parts):
        raise BootstrapFilesError(f"bootstrap input path is not canonical: {path}")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor, absolute.name
    except Exception:
        os.close(descriptor)
        raise


def _write_nofollow(path: Path, content: bytes, *, private: bool) -> None:
    descriptor, name = _open_parent(path)
    mode = 0o600 if private else 0o644
    try:
        try:
            file_descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=descriptor,
            )
        except FileExistsError:
            file_descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            try:
                metadata = os.fstat(file_descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise BootstrapFilesError(f"bootstrap output is not a regular file: {path}")
                if private and metadata.st_mode & 0o077:
                    raise BootstrapFilesError(f"private bootstrap file is too permissive: {path}")
                existing = bytearray()
                while chunk := os.read(file_descriptor, 1024 * 1024):
                    existing.extend(chunk)
                if bytes(existing) != content:
                    raise BootstrapFilesError(f"refusing to overwrite conflicting file: {path}")
            finally:
                os.close(file_descriptor)
            return
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(file_descriptor, content[offset:])
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
    finally:
        os.close(descriptor)


def write_private_file(path: Path, content: bytes) -> None:
    """Create a private file with no-follow and creation-time 0600 semantics."""

    try:
        _write_nofollow(path, content, private=True)
    except OSError as error:
        raise BootstrapFilesError(f"cannot safely write bootstrap file: {path}") from error


def read_private_file(path: Path) -> bytes:
    try:
        parent, name = _open_existing_parent(path)
    except OSError as error:
        raise BootstrapFilesError(f"cannot safely read bootstrap file: {path}") from error
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError as error:
        os.close(parent)
        raise BootstrapFilesError(f"cannot safely read bootstrap file: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BootstrapFilesError(f"bootstrap session is not a regular file: {path}")
        if metadata.st_mode & 0o777 != 0o600:
            raise BootstrapFilesError(f"bootstrap session must have mode 0600: {path}")
        return os.read(descriptor, 1024 * 1024)
    finally:
        os.close(descriptor)
        os.close(parent)


def plan_files(
    exchange: dict[str, Any],
    *,
    root: Path,
    credential_dir: Path,
    repo_id: str,
    sprintctl_version: str,
    expected_environment_id: str | None = None,
    expected_endpoint: str | None = None,
    install_mode: str = "print",
) -> BootstrapFilePlan:
    """Render intended changes without touching disk.

    Existing files are checked by :func:`apply_plan`; the planner itself is
    pure so print-only and agent mode cannot accidentally mutate a repository.
    """

    if install_mode not in {"print", "tool", "none"}:
        raise BootstrapFilesError("install_mode must be print, tool, or none")
    if not isinstance(sprintctl_version, str) or not _IMMUTABLE_VERSION.fullmatch(
        sprintctl_version
    ):
        raise BootstrapFilesError(
            "sprintctl_version must be a concrete immutable release version"
        )
    if not repo_id or not isinstance(repo_id, str):
        raise BootstrapFilesError("repo_id is required")
    token = exchange.get("token")
    workspace_id = exchange.get("workspace_id")
    project_id = exchange.get("project_id")
    if not all(isinstance(value, str) and value for value in (token, workspace_id, project_id)):
        raise BootstrapFilesError("exchange must include token, workspace_id, and project_id")
    if exchange.get("repo_id") != repo_id:
        raise BootstrapFilesError("exchange repository does not match the requested repository")
    profile = exchange.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("target"), dict):
        raise BootstrapFilesError("exchange profile is incomplete")
    target = profile["target"]
    endpoint = target.get("endpoint")
    environment_id = target.get("environment_id")
    if not all(isinstance(value, str) and value for value in (endpoint, environment_id)):
        raise BootstrapFilesError("exchange profile target is incomplete")
    if expected_environment_id is not None and environment_id != expected_environment_id:
        raise BootstrapFilesError("exchange profile environment does not match discovery")
    if profile.get("schema_version") != "vuoro-client-profile/v1":
        raise BootstrapFilesError("exchange profile has an unsupported schema")
    endpoint = _assert_https_endpoint(endpoint, "exchange profile endpoint")
    if expected_endpoint is not None and endpoint != expected_endpoint.rstrip("/"):
        raise BootstrapFilesError("exchange profile endpoint does not match discovery")
    credential_path = credential_dir / "vuoro-cloud.token"
    profile_path = root / ".vuoro" / "profile.json"
    files = {
        root / ".vuoro" / "project.json": _json(
            {
                "schema_version": "vuoro-project/v1",
                "project_id": project_id,
                "home_repo": repo_id,
                "members": [{"repo_id": repo_id}],
                "workspace_id": workspace_id,
            }
        ),
        root / ".sprintctl" / "backend.json": _json(
            {"backend": "served", "repo_id": repo_id}
        ),
        profile_path: _json(
            {
                "schema_version": "vuoro-client-profile/v1",
                "id": "vuoro-cloud",
                "revision": 1,
                "source_environment_id": "developer-workstation",
                "target": {
                    "environment_id": environment_id,
                    "environment_class": "production",
                    "endpoint": endpoint,
                },
                "credential_ref": f"file:{credential_path}",
                "required_authorities": ["work:read", "work:write"],
                "production_endpoint_denied": False,
            }
        ),
        credential_path: (token.rstrip() + "\n").encode(),
    }
    commands: tuple[str, ...] = ()
    if install_mode in {"print", "tool"}:
        commands = (
            f"uv tool install --python 3.12 'sprintctl[served]=={sprintctl_version}'",
        )
    return BootstrapFilePlan(files=files, commands=commands)


def apply_plan(plan: BootstrapFilePlan) -> None:
    """Apply only new or byte-identical files; conflicting files fail closed."""

    for path, content in plan.files.items():
        _assert_no_symlink(path)
        try:
            _write_nofollow(
                path,
                content,
                private=path.name == "vuoro-cloud.token",
            )
        except OSError as error:
            raise BootstrapFilesError(f"cannot safely write bootstrap file: {path}") from error
