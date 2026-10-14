"""Installed single-host publication history, DATA only; never an execution grant.

A fresh private read, actual artifact/signature checks and provider admission
belong to the future executor. A spent marker's first-insert signal reports
local durability only. It never authorizes a send or permits retransmission.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from functools import wraps
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from urllib.parse import urlsplit

from .protected_copy_codec import (
    COPY_MAX,
    _assert_effect_data,
    _canonical,
    _closed,
    _digest,
    _hash,
    _integer,
    _need,
    _read,
    _text,
    decode_protected_copy,
)

DB_NAME = "journal.sqlite3"
LOCK_NAME = "journal.lock"
VERSION = 1
_TABLE_SQL = {
    "journal_metadata": "CREATE TABLE journal_metadata (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1), component_version INTEGER NOT NULL CHECK(component_version=1), host_id TEXT NOT NULL, registry_utf8 BLOB NOT NULL, registry_sha256 TEXT NOT NULL)",
    "effect_bindings": "CREATE TABLE effect_bindings (effect_key TEXT PRIMARY KEY NOT NULL, binding_utf8 BLOB NOT NULL, binding_sha256 TEXT NOT NULL, protected_copy_utf8 BLOB NOT NULL, protected_copy_sha256 TEXT NOT NULL)",
    "journal_events": "CREATE TABLE journal_events (effect_key TEXT NOT NULL REFERENCES effect_bindings(effect_key), seq INTEGER NOT NULL CHECK(seq>=0), event_type TEXT NOT NULL, payload_utf8 BLOB NOT NULL, payload_sha256 TEXT NOT NULL, PRIMARY KEY(effect_key,seq))",
    "publication_spent": "CREATE TABLE publication_spent (effect_key TEXT NOT NULL REFERENCES effect_bindings(effect_key), operation TEXT NOT NULL CHECK(operation IN ('branch-create','pr-create')), request_utf8 BLOB NOT NULL, request_sha256 TEXT NOT NULL, PRIMARY KEY(effect_key,operation))",
    "applied_outcomes": "CREATE TABLE applied_outcomes (effect_key TEXT PRIMARY KEY NOT NULL REFERENCES effect_bindings(effect_key), outcome_utf8 BLOB NOT NULL, outcome_sha256 TEXT NOT NULL)",
}
_TRIGGER_SQL = {
    f"{table}_no_{verb.lower()}": (
        f"CREATE TRIGGER {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'immutable-journal'); END"
    )
    for table in _TABLE_SQL
    for verb in ("UPDATE", "DELETE")
}
_CATALOG = sorted(
    [("table", name, name, sql) for name, sql in _TABLE_SQL.items()]
    + [
        ("trigger", name, name.rsplit("_no_", 1)[0], sql)
        for name, sql in _TRIGGER_SQL.items()
    ]
    + [
        ("index", f"sqlite_autoindex_{name}_1", name, None)
        for name in _TABLE_SQL
        if name != "journal_metadata"
    ]
)
_MUTEX_GUARD = threading.Lock()
_MUTEXES = {}
_HEX = re.compile(r"[0-9a-f]{64}")
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_ARTIFACT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*")
_BINDING_FIELDS = (
    "schema namespace intent_id intent_revision canonical_hash legacy_content_digest "
    "acceptance_id acceptance_revision preparation_id receipt_id accepted_at "
    "signature_sha256 challenge_sha256 frozen_reference_set_sha256 registry_sha256 "
    "provider_profile_sha256 signing_profile_sha256 publication_target signer_fingerprint public_key_sha256"
)
_PAYLOAD_FIELDS = {
    "LOCAL_READY": "schema commit_oid tree_oid base_parent_oid message_utf8_b64 message_sha256 signer_fingerprint public_key_sha256 artifact_relative_path artifact_sha256",
    "BRANCH_TRANSMISSION_POSSIBLE": "schema effect_key binding_sha256 repository branch commit_oid base_parent_oid provider_profile_sha256",
    "PR_TRANSMISSION_POSSIBLE": "schema effect_key binding_sha256 repository head_repository branch commit_oid base_branch title body_utf8_b64 body_sha256 provider_profile_sha256",
    "BRANCH_OBSERVED": "schema effect_key binding_sha256 request_sha256 repository branch commit_oid",
    "PR_OBSERVED": "schema effect_key binding_sha256 request_sha256 repository head_repository branch commit_oid base_branch title body_utf8_b64 body_sha256 pr_number pr_url state",
    "APPLIED": "schema effect_key binding_sha256 commit_oid branch_request_sha256 pr_request_sha256 pr_number pr_url",
    "REFUSED": "schema effect_key binding_sha256 reason operation request_sha256",
    "CONFLICT": "schema effect_key binding_sha256 reason operation request_sha256",
}
_SCHEMAS = dict(
    zip(
        _PAYLOAD_FIELDS,
        (
            "vuoro-protected-local-ready/v1",
            "vuoro-protected-branch-request/v1",
            "vuoro-protected-pr-request/v1",
            "vuoro-protected-branch-observation/v1",
            "vuoro-protected-pr-observation/v1",
            "vuoro-protected-applied-outcome/v1",
            "vuoro-protected-journal-terminal/v1",
            "vuoro-protected-journal-terminal/v1",
        ),
    )
)
_OPERATION_EVENTS = {
    "branch-create": "BRANCH_TRANSMISSION_POSSIBLE",
    "pr-create": "PR_TRANSMISSION_POSSIBLE",
}
_TERMINAL = {"APPLIED", "REFUSED", "CONFLICT"}


class JournalRefused(ValueError):
    """Fixed diagnostic only; no upstream error or stored content."""


def _fixed(function):
    @wraps(function)
    def checked(*args, **kwargs):
        failed = False
        try:
            return function(*args, **kwargs)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            sqlite3.Error,
            RuntimeError,
            UnicodeError,
            RecursionError,
            OverflowError,
        ):
            failed = True
        if failed:
            raise JournalRefused("execution-journal-invalid")

    return checked


@dataclass(frozen=True, slots=True, repr=False)
class JournalEvent:
    seq: int
    event_type: str
    payload_utf8: bytes


@dataclass(frozen=True, slots=True, repr=False)
class SpentRecord:
    operation: str
    request_utf8: bytes


@dataclass(frozen=True, slots=True, repr=False)
class JournalRecord:
    effect_key: str
    binding_utf8: bytes
    protected_copy_utf8: bytes
    events: tuple[JournalEvent, ...]
    spent: tuple[SpentRecord, ...]
    outcome_utf8: bytes | None

    @property
    def status(self):
        last = self.events[-1].event_type
        if last in _TERMINAL:
            return last
        return "UNSETTLED" if self.spent else last


def _caller():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _path(path):
    _need(isinstance(path, (str, Path)))
    path = Path(os.path.abspath(path))
    for parent in reversed((path, *path.parents)):
        _need(not stat.S_ISLNK(os.lstat(parent).st_mode))
    return path


def _identity(info):
    return info.st_dev, info.st_ino


def _owned(info, mode, directory=False):
    _need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == mode)
    _need(
        stat.S_ISDIR(info.st_mode)
        if directory
        else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    )


def _settings(connection):
    _need(connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    _need(connection.execute("PRAGMA synchronous").fetchone()[0] == 2)
    _need(connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1)


def _catalog(connection):
    _need(connection.execute("PRAGMA user_version").fetchone()[0] == VERSION)
    found = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    _need(found == _CATALOG)
    _need(connection.execute("PRAGMA foreign_key_check").fetchall() == [])
    _need(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)])


def _fsync_files(directory_fd, database_fd):
    os.fsync(database_fd)
    os.fsync(directory_fd)


@_fixed
def install_journal(directory, *, host_id, registry_utf8):
    """Exclusive explicit installation, never a runtime repair or activation."""
    _text(host_id, 200)
    _read(registry_utf8, 64 * 1024)
    directory = Path(os.path.abspath(directory))
    _path(directory.parent)
    parent_fd = directory_fd = lock_fd = database_fd = None
    connection = None
    try:
        parent_fd = os.open(
            directory.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        parent_identity = _identity(os.fstat(parent_fd))
        _need(_identity(os.lstat(directory.parent)) == parent_identity)
        os.mkdir(directory.name, 0o700, dir_fd=parent_fd)
        directory_fd = os.open(
            directory.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        _owned(os.fstat(directory_fd), 0o700, True)
        for name in (LOCK_NAME, DB_NAME):
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
            if name == LOCK_NAME:
                lock_fd = fd
            else:
                database_fd = fd
        connection = sqlite3.connect(
            f"file:/proc/self/fd/{directory_fd}/{DB_NAME}?mode=rw",
            uri=True,
            isolation_level=None,
        )
        _settings(connection)
        connection.execute("BEGIN IMMEDIATE")
        for sql in (*_TABLE_SQL.values(), *_TRIGGER_SQL.values()):
            connection.execute(sql)
        connection.execute(f"PRAGMA user_version={VERSION}")
        connection.execute(
            "INSERT INTO journal_metadata VALUES (1,1,?,?,?)",
            (host_id, registry_utf8, _hash(registry_utf8)),
        )
        _catalog(connection)
        connection.commit()
        _fsync_files(directory_fd, database_fd)
        os.fsync(lock_fd)
        os.fsync(directory_fd)
        # Persist the new directory entry on its originally pinned parent.
        _path(directory)
        _need(_identity(os.lstat(directory.parent)) == parent_identity)
        _need(_identity(os.lstat(directory)) == _identity(os.fstat(directory_fd)))
        for fd, name in ((database_fd, DB_NAME), (lock_fd, LOCK_NAME)):
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            _owned(info, 0o600)
            _need(_identity(info) == _identity(os.fstat(fd)))
        os.fsync(parent_fd)
        _need(_identity(os.lstat(directory.parent)) == parent_identity)
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            for fd in (database_fd, lock_fd, directory_fd, parent_fd):
                if fd is not None:
                    os.close(fd)
    # A failed installation is intentionally left for owner inspection; no repair.


def _branch(value):
    _text(value, 200)
    _need(
        value not in ("@", "HEAD")
        and not value.startswith(("/", "-"))
        and not value.endswith(("/", "."))
    )
    _need(not any(ord(c) <= 32 or ord(c) == 127 or c in "~^:?*[\\" for c in value))
    _need(".." not in value and "@{" not in value and "//" not in value)
    _need(
        all(
            not part.startswith(".") and not part.endswith(".lock")
            for part in value.split("/")
        )
    )


def _b64(value, maximum):
    _text(value, 4 * ((maximum + 2) // 3), empty=True)
    raw = base64.b64decode(value, validate=True)
    _need(len(raw) <= maximum and base64.b64encode(raw).decode("ascii") == value)
    raw.decode("utf8")
    return raw


def _fingerprint(value):
    _need(type(value) is str and _OID.fullmatch(value))


def _decode_copy(raw):
    copy = _read(raw, COPY_MAX)
    co = _read(base64.b64decode(copy["canonical_utf8_b64"], validate=True))
    return decode_protected_copy(
        raw,
        namespace_utf8=_canonical(copy["locator"]["namespace"]),
        acceptance_id=copy["acceptance"]["acceptance_id"],
        repository=co["parameters"]["repository"],
    )


def _binding(
    effect,
    registry_hash,
    provider_hash,
    signing_hash,
    target,
    fingerprint,
    public_key_hash,
):
    _assert_effect_data(effect)
    for digest in (registry_hash, provider_hash, signing_hash, public_key_hash):
        _digest(digest)
    _fingerprint(fingerprint)
    _closed(target, "schema repository head_repository base_branch")
    _need(target["schema"] == "vuoro-protected-publication-target/v1")
    _need(target["repository"] == target["head_repository"] == effect.repository)
    _branch(target["base_branch"])
    locator = _read(effect.locator_utf8)
    value = {
        "schema": "vuoro-protected-journal-binding/v1",
        "namespace": locator["namespace"],
        "intent_id": locator["intent_id"],
        "intent_revision": locator["intent_revision"],
        "canonical_hash": effect.canonical_hash,
        "legacy_content_digest": effect.legacy_content_digest,
        "acceptance_id": effect.acceptance.acceptance_id,
        "acceptance_revision": effect.acceptance_revision,
        "preparation_id": effect.acceptance.preparation_id,
        "receipt_id": effect.receipt.receipt_id,
        "accepted_at": effect.acceptance.accepted_at,
        "signature_sha256": effect.acceptance.signature_sha256,
        "challenge_sha256": effect.acceptance.challenge_sha256,
        "frozen_reference_set_sha256": _hash(
            _canonical(_read(effect.canonical_utf8)["evidence_refs"])
        ),
        "registry_sha256": registry_hash,
        "provider_profile_sha256": provider_hash,
        "signing_profile_sha256": signing_hash,
        "publication_target": target,
        "signer_fingerprint": fingerprint,
        "public_key_sha256": public_key_hash,
    }
    _closed(value, _BINDING_FIELDS)
    return value


def effect_key(effect):
    """Deterministic DATA identity; neither this nor a DTO is a grant."""
    _assert_effect_data(effect)
    locator = _read(effect.locator_utf8)
    return _hash(
        _canonical(
            {
                "schema": "vuoro-protected-effect-key/v1",
                **{
                    k: locator[k] for k in ("namespace", "intent_id", "intent_revision")
                },
            }
        ),
        False,
    )


def _public_binding(record, binding, local, effect):
    return {
        "schema": "vuoro-protected-publication-binding/v1",
        "effect_key": record.effect_key,
        "binding_sha256": _hash(record.binding_utf8),
        "locator_utf8_b64": base64.b64encode(effect.locator_utf8).decode("ascii"),
        "canonical_hash": binding["canonical_hash"],
        "legacy_content_digest": binding["legacy_content_digest"],
        "acceptance_id": binding["acceptance_id"],
        "acceptance_revision": binding["acceptance_revision"],
        "receipt_id": binding["receipt_id"],
        "source_run_utf8_b64": base64.b64encode(
            effect.source_run.encode("utf8")
        ).decode("ascii"),
        "commit_oid": local["commit_oid"],
    }


def _payload(record, event_type, raw, seen, effect, binding):
    limits = {
        "LOCAL_READY": 16384,
        "BRANCH_TRANSMISSION_POSSIBLE": 16384,
        "PR_TRANSMISSION_POSSIBLE": 65536,
        "PR_OBSERVED": 65536,
    }
    value = _read(raw, limits.get(event_type, 8192))
    _closed(value, _PAYLOAD_FIELDS[event_type])
    _need(value["schema"] == _SCHEMAS[event_type])
    parameters = _read(effect.parameters_utf8)
    target = binding["publication_target"]
    local = seen.get("LOCAL_READY")
    if event_type == "LOCAL_READY":
        for field in ("commit_oid", "tree_oid", "base_parent_oid"):
            _fingerprint(value[field])
            _need(len(value[field]) == len(parameters["base_commit"]))
        _need(value["base_parent_oid"] == parameters["base_commit"])
        _need(
            value["signer_fingerprint"] == binding["signer_fingerprint"]
            and value["public_key_sha256"] == binding["public_key_sha256"]
        )
        _digest(value["artifact_sha256"])
        _text(value["artifact_relative_path"], 1024)
        _need(_ARTIFACT.fullmatch(value["artifact_relative_path"]))
        message = _b64(value["message_utf8_b64"], 16384)
        _need(value["message_sha256"] == _hash(message))
        return value
    _need(
        value["effect_key"] == record.effect_key
        and value["binding_sha256"] == _hash(record.binding_utf8)
    )
    if event_type in ("REFUSED", "CONFLICT"):
        allowed = {
            "REFUSED": {"original-pr-closed"},
            "CONFLICT": {
                "original-branch-mismatch",
                "original-pr-mismatch",
                "original-artifact-mismatch",
            },
        }
        _need(value["reason"] in allowed[event_type])
        if value["reason"] == "original-artifact-mismatch":
            _need(
                value["operation"] is None
                and value["request_sha256"] is None
                and local is not None
            )
        else:
            op = (
                "branch-create"
                if value["reason"] == "original-branch-mismatch"
                else "pr-create"
            )
            _need(value["operation"] == op)
            request_type = _OPERATION_EVENTS[op]
            _need(
                request_type in seen
                and value["request_sha256"] == _hash(_canonical(seen[request_type]))
            )
        if event_type == "REFUSED":
            _need("PR_OBSERVED" in seen and seen["PR_OBSERVED"]["state"] == "closed")
        return value
    _need(local is not None and value["commit_oid"] == local["commit_oid"])
    if "repository" in value:
        _need(value["repository"] == effect.repository)
    if "branch" in value:
        _need(value["branch"] == "vuoro-protected/" + record.effect_key)
    if "head_repository" in value:
        _need(value["head_repository"] == target["head_repository"])
    if "base_branch" in value:
        _need(value["base_branch"] == target["base_branch"])
    if "provider_profile_sha256" in value:
        _need(value["provider_profile_sha256"] == binding["provider_profile_sha256"])
    if event_type == "BRANCH_TRANSMISSION_POSSIBLE":
        _need(value["base_parent_oid"] == local["base_parent_oid"])
    elif event_type == "PR_TRANSMISSION_POSSIBLE":
        _need("BRANCH_OBSERVED" in seen and value["title"] == parameters["title"])
        public = _canonical(_public_binding(record, binding, local, effect))
        _need(len(public) <= 8192)
        expected = (
            parameters["rationale"]
            + "\n\n<!-- vuoro-protected-publication/v1 "
            + base64.b64encode(public).decode("ascii")
            + " -->\n"
        ).encode("utf8")
        body = _b64(value["body_utf8_b64"], 32768)
        _need(body == expected and value["body_sha256"] == _hash(body))
    elif event_type in ("BRANCH_OBSERVED", "PR_OBSERVED"):
        request_type = (
            "BRANCH_TRANSMISSION_POSSIBLE"
            if event_type == "BRANCH_OBSERVED"
            else "PR_TRANSMISSION_POSSIBLE"
        )
        _need(request_type in seen)
        request = seen[request_type]
        _need(value["request_sha256"] == _hash(_canonical(request)))
        for key in set(value) & set(request) - {"schema"}:
            _need(value[key] == request[key])
        if event_type == "PR_OBSERVED":
            _integer(value["pr_number"])
            _text(value["pr_url"], 2048)
            url = urlsplit(value["pr_url"])
            _need(
                url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and not any(ord(c) <= 32 or ord(c) == 127 for c in value["pr_url"])
            )
            _need(value["state"] in ("open", "merged", "closed"))
    elif event_type == "APPLIED":
        _need(
            "PR_OBSERVED" in seen and seen["PR_OBSERVED"]["state"] in ("open", "merged")
        )
        for op, field in (
            ("branch-create", "branch_request_sha256"),
            ("pr-create", "pr_request_sha256"),
        ):
            _need(value[field] == _hash(_canonical(seen[_OPERATION_EVENTS[op]])))
        _need(
            value["pr_number"] == seen["PR_OBSERVED"]["pr_number"]
            and type(value["pr_number"]) is int
        )
        _need(value["pr_url"] == seen["PR_OBSERVED"]["pr_url"])
    return value


class ExecutionJournal:
    """Existing installed local journal, never a source of execution authority."""

    @_fixed
    def __init__(self, directory, *, host_id, registry_utf8):
        _text(host_id, 200)
        _read(registry_utf8, 65536)
        self.directory = _path(directory)
        self.host_id, self.registry_utf8 = host_id, registry_utf8
        self._fds = []
        self._pid = os.getpid()
        self._active = None
        self._connection = None
        self._uncertain = False
        try:
            self._directory_fd = os.open(
                self.directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            self._fds.append(self._directory_fd)
            for attr, name in (("_lock_fd", LOCK_NAME), ("_database_fd", DB_NAME)):
                fd = os.open(
                    name,
                    os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=self._directory_fd,
                )
                setattr(self, attr, fd)
                self._fds.append(fd)
            self._identities = tuple(_identity(os.fstat(fd)) for fd in self._fds)
            self._check_files()
            mutex_key = (os.getpid(), *self._identities[1])
            with _MUTEX_GUARD:
                self._mutex_entry = _MUTEXES.setdefault(
                    mutex_key, [threading.Lock(), None]
                )
            with self.cycle():
                with self._transaction(write=False):
                    pass
        except BaseException:
            self.close()
            raise

    def close(self):
        _need(self._active is None)
        for fd in self._fds:
            os.close(fd)
        self._fds = []

    def _check_files(self):
        _need(os.getpid() == self._pid and len(self._fds) == 3)
        _path(self.directory)
        path_stats = [
            os.lstat(self.directory),
            os.stat(LOCK_NAME, dir_fd=self._directory_fd, follow_symlinks=False),
            os.stat(DB_NAME, dir_fd=self._directory_fd, follow_symlinks=False),
        ]
        for index, (fd, current) in enumerate(zip(self._fds, path_stats)):
            opened = os.fstat(fd)
            _owned(current, 0o700 if index == 0 else 0o600, index == 0)
            _owned(opened, 0o700 if index == 0 else 0o600, index == 0)
            _need(_identity(opened) == _identity(current) == self._identities[index])

    @contextmanager
    def cycle(self):
        caller = _caller()
        failure = False
        locked = False
        acquired = False
        try:
            _need(
                os.getpid() == self._pid
                and self._active != caller
                and not self._uncertain
            )
            with _MUTEX_GUARD:
                owner = self._mutex_entry[1]
                _need(owner is None or owner[:2] != caller[:2])
            self._mutex_entry[0].acquire()
            acquired = True
            with _MUTEX_GUARD:
                self._mutex_entry[1] = caller
            self._check_files()
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            locked = True
            self._check_files()
            self._active = caller
            self._connection = sqlite3.connect(
                f"file:/proc/self/fd/{self._directory_fd}/{DB_NAME}?mode=rw",
                uri=True,
                isolation_level=None,
            )
            _settings(self._connection)
            yield self
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
            sqlite3.Error,
            RuntimeError,
        ):
            failure = True
        finally:
            if acquired:
                try:
                    if self._connection is not None:
                        self._connection.close()
                except (OSError, sqlite3.Error, RuntimeError):
                    failure = True
                    self._uncertain = True
                finally:
                    self._connection = None
                    self._active = None
                    try:
                        if locked:
                            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        failure = True
                        self._uncertain = True
                    finally:
                        with _MUTEX_GUARD:
                            self._mutex_entry[1] = None
                        self._mutex_entry[0].release()
        if failure:
            raise JournalRefused("execution-journal-invalid")

    def _held(self):
        _need(
            os.getpid() == self._pid
            and self._active == _caller()
            and self._connection is not None
            and not self._uncertain
        )

    def _commit(self):
        self._connection.commit()
        _fsync_files(self._directory_fd, self._database_fd)

    @contextmanager
    def _transaction(self, *, write=True):
        self._held()
        self._check_files()
        self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            _catalog(self._connection)
            rows = self._connection.execute("SELECT * FROM journal_metadata").fetchall()
            _need(
                rows
                == [
                    (
                        1,
                        VERSION,
                        self.host_id,
                        self.registry_utf8,
                        _hash(self.registry_utf8),
                    )
                ]
            )
            self._validate_all()
            yield self._connection
            self._validate_all()
            self._check_files()
            if write:
                try:
                    self._commit()
                    self._check_files()
                except BaseException:
                    self._uncertain = True
                    raise
            else:
                self._connection.rollback()
            self._check_files()
        except BaseException:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise

    def _load(self, key):
        row = self._connection.execute(
            "SELECT binding_utf8,binding_sha256,protected_copy_utf8,protected_copy_sha256 FROM effect_bindings WHERE effect_key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        binding, binding_hash, copy, copy_hash = row
        _need(
            type(binding) is bytes
            and type(copy) is bytes
            and binding_hash == _hash(binding)
            and copy_hash == _hash(copy)
        )
        events = []
        for seq, kind, raw, digest in self._connection.execute(
            "SELECT seq,event_type,payload_utf8,payload_sha256 FROM journal_events WHERE effect_key=? ORDER BY seq",
            (key,),
        ):
            _need(type(raw) is bytes and digest == _hash(raw) and type(seq) is int)
            events.append(JournalEvent(seq, kind, raw))
        spent = []
        for operation, raw, digest in self._connection.execute(
            "SELECT operation,request_utf8,request_sha256 FROM publication_spent WHERE effect_key=? ORDER BY operation",
            (key,),
        ):
            _need(type(raw) is bytes and digest == _hash(raw))
            spent.append(SpentRecord(operation, raw))
        outcome = self._connection.execute(
            "SELECT outcome_utf8,outcome_sha256 FROM applied_outcomes WHERE effect_key=?",
            (key,),
        ).fetchone()
        if outcome is not None:
            _need(type(outcome[0]) is bytes and outcome[1] == _hash(outcome[0]))
        return JournalRecord(
            key,
            binding,
            copy,
            tuple(events),
            tuple(spent),
            None if outcome is None else outcome[0],
        )

    def _verify(self, record):
        _need(type(record.effect_key) is str and _HEX.fullmatch(record.effect_key))
        binding = _read(record.binding_utf8, 16384)
        _closed(binding, _BINDING_FIELDS)
        effect = _decode_copy(record.protected_copy_utf8)
        _need(record.effect_key == effect_key(effect))
        expected = _binding(
            effect,
            _hash(self.registry_utf8),
            binding["provider_profile_sha256"],
            binding["signing_profile_sha256"],
            binding["publication_target"],
            binding["signer_fingerprint"],
            binding["public_key_sha256"],
        )
        _need(record.binding_utf8 == _canonical(expected) and record.events)
        seen = {}
        for seq, event in enumerate(record.events):
            _need(
                event.seq == seq
                and event.event_type not in seen
                and not (_TERMINAL & seen.keys())
            )
            if seq == 0:
                _need(
                    event.event_type == "BOUND"
                    and event.payload_utf8 == record.binding_utf8
                )
                seen["BOUND"] = binding
            else:
                _need(event.event_type in _PAYLOAD_FIELDS)
                value = _payload(
                    record, event.event_type, event.payload_utf8, seen, effect, binding
                )
                if event.event_type == "LOCAL_READY":
                    _need(set(seen) == {"BOUND"})
                seen[event.event_type] = value
        expected_spent = {
            op: _canonical(seen[kind])
            for op, kind in _OPERATION_EVENTS.items()
            if kind in seen
        }
        _need({r.operation: r.request_utf8 for r in record.spent} == expected_spent)
        _need(
            record.outcome_utf8
            == (_canonical(seen["APPLIED"]) if "APPLIED" in seen else None)
        )
        return seen, effect, binding

    def _validate_all(self):
        keys = {
            row[0]
            for row in self._connection.execute(
                "SELECT effect_key FROM effect_bindings"
            )
        }
        for table in ("journal_events", "publication_spent", "applied_outcomes"):
            _need(
                {
                    row[0]
                    for row in self._connection.execute(
                        f"SELECT DISTINCT effect_key FROM {table}"
                    )
                }
                <= keys
            )
        for key in keys:
            self._verify(self._load(key))

    def _event(self, key, kind, raw, seq):
        self._connection.execute(
            "INSERT INTO journal_events VALUES (?,?,?,?,?)",
            (key, seq, kind, raw, _hash(raw)),
        )

    @_fixed
    def bind(
        self,
        effect,
        *,
        protected_copy_utf8,
        provider_profile_sha256,
        signing_profile_sha256,
        publication_target_utf8,
        expected_signer_fingerprint,
        expected_public_key_sha256,
    ):
        _need(_decode_copy(protected_copy_utf8) == effect)
        binding = _canonical(
            _binding(
                effect,
                _hash(self.registry_utf8),
                provider_profile_sha256,
                signing_profile_sha256,
                _read(publication_target_utf8, 16384),
                expected_signer_fingerprint,
                expected_public_key_sha256,
            )
        )
        _need(len(binding) <= 16384)
        key = effect_key(effect)
        with self._transaction() as connection:
            current = self._load(key)
            if current is None:
                connection.execute(
                    "INSERT INTO effect_bindings VALUES (?,?,?,?,?)",
                    (
                        key,
                        binding,
                        _hash(binding),
                        protected_copy_utf8,
                        _hash(protected_copy_utf8),
                    ),
                )
                self._event(key, "BOUND", binding, 0)
            else:
                _need(
                    current.binding_utf8 == binding
                    and current.protected_copy_utf8 == protected_copy_utf8
                )
            return self._load(key)

    @_fixed
    def read(self, key):
        _need(type(key) is str and _HEX.fullmatch(key))
        with self._transaction(write=False):
            return self._load(key)

    def _append(self, key, kind, raw, *, operation=None, outcome=False):
        with self._transaction() as connection:
            record = self._load(key)
            _need(record is not None)
            seen, effect, binding = self._verify(record)
            if kind in seen:
                _need(_canonical(seen[kind]) == raw)
                return False
            _need(not (_TERMINAL & seen.keys()))
            value = _payload(record, kind, raw, seen, effect, binding)
            if kind == "LOCAL_READY":
                _need(set(seen) == {"BOUND"})
            _need(value is not None)
            if operation is not None:
                connection.execute(
                    "INSERT INTO publication_spent VALUES (?,?,?,?)",
                    (key, operation, raw, _hash(raw)),
                )
            if outcome:
                connection.execute(
                    "INSERT INTO applied_outcomes VALUES (?,?,?)",
                    (key, raw, _hash(raw)),
                )
            self._event(key, kind, raw, len(record.events))
            return True

    @_fixed
    def record_local_ready(self, key, artifact_utf8):
        self._append(key, "LOCAL_READY", artifact_utf8)
        return self.read(key)

    @_fixed
    def mark_spent(self, key, operation, request_utf8):
        """True is a durable first-insert observation, NEVER authorization."""
        _need(operation in _OPERATION_EVENTS)
        return self._append(
            key, _OPERATION_EVENTS[operation], request_utf8, operation=operation
        )

    @_fixed
    def record_observed(self, key, event_type, payload_utf8):
        _need(event_type in ("BRANCH_OBSERVED", "PR_OBSERVED", "REFUSED", "CONFLICT"))
        self._append(key, event_type, payload_utf8)
        return self.read(key)

    @_fixed
    def record_outcome(self, key, outcome_utf8):
        self._append(key, "APPLIED", outcome_utf8, outcome=True)
        return self.read(key)
