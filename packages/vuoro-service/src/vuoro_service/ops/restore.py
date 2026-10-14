"""Isolated restore: a pg_dump into a throwaway PostgreSQL, then the pinned check.

The operation accepts a dump *file* and never a connection string.  It
initializes its own cluster in a fresh temporary directory, listening only on
127.0.0.1 at a free port with trust authentication, restores into it, runs
the pinned composition's read-only startup compatibility checks against the
restored state, and deletes the cluster.  A live database is unreachable by
construction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from typing import Any, Iterator
from urllib.parse import quote

from .result import DiagnosticResult, load_pins
from .runtime import ImageRunner, ProbeError, probe_source, run_in_image, run_in_process

_NIX_PG16 = Path("/nix/store/lkcp4yn4dmkhmngwa363wwqyhhk5rzs4-postgresql-16.15/bin")
_ADMIN = "vuoro_ops_admin"
_DATABASE = "vuoro_restore"
_AGE_MAGIC = b"age-encryption.org/"
_CUSTOM_MAGIC = b"PGDMP"
_IDENT = r'(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_$]*))'
_ROLE_PATTERNS = (
    re.compile(r"OWNER TO " + _IDENT),
    re.compile(r"FOR ROLE " + _IDENT),
)
_GRANT = re.compile(r"\b(?:GRANT|REVOKE)\b[^;]*?\b(?:TO|FROM) ([^;]+);")

ProbeRunner = Callable[[str, Mapping[str, str]], dict[str, Any]]


class RestoreRefused(ValueError):
    """The input is not something this operation restores."""


def pg_bin_dir(explicit: Path | None = None) -> Path:
    candidates = [explicit, Path(os.environ["VUORO_PG_BIN"]) if os.environ.get("VUORO_PG_BIN") else None]
    found = shutil.which("initdb")
    candidates += [Path(found).parent if found else None, _NIX_PG16,
                   *sorted(Path("/usr/lib/postgresql").glob("16/bin")), *sorted(Path("/usr/lib/postgresql").glob("*/bin"))]
    for candidate in candidates:
        if candidate is not None and (candidate / "initdb").is_file() and (candidate / "pg_restore").is_file():
            return candidate
    raise RestoreRefused("no PostgreSQL binaries (initdb, pg_ctl, pg_restore) found; set VUORO_PG_BIN")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=600, **kwargs)


@contextmanager
def disposable_cluster(bin_dir: Path, *, keep: bool = False) -> Iterator[dict[str, Any]]:
    root = Path(tempfile.mkdtemp(prefix="vuoro-ops-restore-"))
    data, socket_dir = root / "data", root / "socket"
    socket_dir.mkdir(mode=0o700)
    port = _free_port()
    init = _run([str(bin_dir / "initdb"), "-D", str(data), "-U", _ADMIN, "-A", "trust",
                 "-E", "UTF8", "--locale=C", "--no-sync"])
    if init.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise RestoreRefused(f"initdb failed: {init.stderr.strip()[-300:]}")
    options = (f"-c listen_addresses=127.0.0.1 -c port={port} -c unix_socket_directories={socket_dir} "
               "-c fsync=off -c synchronous_commit=off -c full_page_writes=off")
    start = _run([str(bin_dir / "pg_ctl"), "-D", str(data), "-o", options, "-l", str(root / "server.log"),
                  "-w", "-t", "60", "start"])
    if start.returncode != 0:
        log = (root / "server.log").read_text(errors="replace")[-400:] if (root / "server.log").exists() else ""
        shutil.rmtree(root, ignore_errors=True)
        raise RestoreRefused(f"disposable PostgreSQL did not start: {log}")
    cluster = {"root": str(root), "port": port, "host": "127.0.0.1", "admin": _ADMIN,
               "server_version": _run([str(bin_dir / "postgres"), "--version"]).stdout.strip()}
    try:
        yield cluster
    finally:
        _run([str(bin_dir / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"])
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
        cluster["torn_down"] = not keep


def _psql(bin_dir: Path, cluster: Mapping[str, Any], sql: str, database: str = "postgres") -> subprocess.CompletedProcess:
    return _run([str(bin_dir / "psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
                 "-p", str(cluster["port"]), "-U", _ADMIN, "-d", database, "-At", "-c", sql])


def _ident(match: re.Match) -> str:
    return match.group(1).replace('""', '"') if match.group(1) is not None else match.group(2)


def dump_roles(schema_sql: str) -> list[str]:
    """Every role a custom dump assigns ownership or privileges to."""
    roles: set[str] = set()
    for pattern in _ROLE_PATTERNS:
        roles.update(_ident(match) for match in pattern.finditer(schema_sql))
    for match in _GRANT.finditer(schema_sql):
        for grantee in match.group(1).split(","):
            grantee = grantee.strip().removesuffix(" WITH GRANT OPTION").strip()
            found = re.fullmatch(_IDENT, grantee)
            if found:
                roles.add(_ident(found))
    roles.discard("PUBLIC")
    roles.discard(_ADMIN)
    return sorted(roles)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _dsn(cluster: Mapping[str, Any], role: str, schema: str | None) -> str:
    dsn = f"postgresql://{quote(role, safe='')}@127.0.0.1:{cluster['port']}/{_DATABASE}"
    if schema:
        dsn += "?options=" + quote(f"-csearch_path={schema}", safe="")
    return dsn


def _installed_satisfies(pins: Mapping[str, str]) -> bool:
    try:
        return all(version(name) == expected for name, expected in pins.items()) and bool(
            importlib.util.find_spec("psycopg"))
    except PackageNotFoundError:
        return False


def isolated_restore(
    dump_path: Path,
    *,
    manifest_path: Path,
    owner_image: str | None = None,
    roles: Mapping[str, str] | None = None,
    schemas: Mapping[str, str] | None = None,
    bin_dir: Path | None = None,
    probe_runner: ProbeRunner | None = None,
    image_runner: ImageRunner | None = None,
    keep: bool = False,
    work_refs: list[dict[str, str]] | None = None,
) -> DiagnosticResult:
    unknowns: list[str] = []
    refs = list(work_refs or [])
    info = dump_path.lstat() if dump_path.exists() or dump_path.is_symlink() else None
    if info is None or dump_path.is_symlink() or not dump_path.is_file():
        return DiagnosticResult("isolated-restore", "1", "error", {"dump": str(dump_path), "refused": "dump must be a regular file"})
    with dump_path.open("rb") as handle:
        head = handle.read(32)
    if head.startswith(_AGE_MAGIC):
        return DiagnosticResult("isolated-restore", "1", "error", {
            "dump": str(dump_path), "refused": "age-encrypted dumps are out of scope; decrypt to a pg_dump -Fc file first"})
    if not head.startswith(_CUSTOM_MAGIC):
        return DiagnosticResult("isolated-restore", "1", "error", {
            "dump": str(dump_path), "refused": "not a pg_dump custom-format (-Fc) archive"})
    digest = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    refs.append({"kind": "dump", "id": f"sha256:{digest}"})
    bins = pg_bin_dir(bin_dir)
    pins_document = load_pins(manifest_path)
    adapter_pins = {
        lock["distribution"]: lock["distribution_version"]
        for lock in pins_document.get("release_locks", []) if lock.get("lock_kind") in ("adapter", "shared-dependency", "owner-dependency")
    }
    domains = [descriptor["domain"] for descriptor in pins_document.get("runtime_descriptors", [])]
    toc = _run([str(bins / "pg_restore"), "-l", str(dump_path)])
    schema_sql = _run([str(bins / "pg_restore"), "-s", "-f", "-", str(dump_path)])
    if toc.returncode != 0 or schema_sql.returncode != 0:
        return DiagnosticResult("isolated-restore", "1", "error", {
            "dump": str(dump_path), "dump_sha256": digest,
            "refused": f"pg_restore cannot read the archive: {(toc.stderr or schema_sql.stderr).strip()[-300:]}"},
            work_refs=refs)
    toc_entries = [line for line in toc.stdout.splitlines() if line and not line.startswith(";")]
    dump_schemas = sorted({m.group(1) for m in re.finditer(r"^CREATE SCHEMA (\S+);", schema_sql.stdout, re.MULTILINE)})
    needed_roles = dump_roles(schema_sql.stdout)

    restore: dict[str, Any] = {
        "dump": str(dump_path), "dump_sha256": digest, "format": "custom",
        "toc_entries": len(toc_entries), "schemas": dump_schemas, "roles_created": needed_roles,
    }
    compatibility: dict[str, Any] = {}
    with disposable_cluster(bins, keep=keep) as cluster:
        restore["server_version"] = cluster["server_version"]
        statements = [f"CREATE ROLE {_quote_ident(role)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" for role in needed_roles]
        statements.append(f"CREATE DATABASE {_DATABASE}")
        for statement in statements:
            created = _psql(bins, cluster, statement)
            if created.returncode != 0:
                restore["setup_error"] = created.stderr.strip()[-300:]
                break
        applied = _run([str(bins / "pg_restore"), "-h", "127.0.0.1", "-p", str(cluster["port"]), "-U", _ADMIN,
                        "-d", _DATABASE, "--exit-on-error", "--single-transaction", "--no-tablespaces",
                        str(dump_path)])
        restore["pg_restore_exit"] = applied.returncode
        if applied.returncode != 0:
            restore["pg_restore_error"] = applied.stderr.strip()[-600:]
        restored_schemas = _psql(bins, cluster, "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
                                 "AND nspname <> 'information_schema' ORDER BY 1", _DATABASE)
        restore["restored_schemas"] = restored_schemas.stdout.split()

        if applied.returncode == 0:
            env = {"VUORO_OPS_PINS": json.dumps(adapter_pins, sort_keys=True)}
            used_roles: dict[str, str] = {}
            for domain in domains:
                role = (roles or {}).get(domain, f"{domain}_runtime")
                schema = (schemas or {}).get(domain, domain)
                if role not in needed_roles:
                    unknowns.append(f"runtime-role-absent-from-dump:{domain}:{role}")
                    role = _ADMIN
                used_roles[domain] = role
                env[f"VUORO_OPS_{domain.upper()}_DSN"] = _dsn(cluster, role, schema)
                env[f"VUORO_OPS_{domain.upper()}_SCHEMA"] = schema
            compatibility["roles"] = used_roles
            mode = None
            try:
                if probe_runner is not None:
                    mode, observation = "injected", probe_runner(probe_source("compatibility"), env)
                elif owner_image is not None:
                    refs.append({"kind": "owner-image", "id": owner_image})
                    mode, observation = "image", (image_runner or run_in_image)(owner_image, probe_source("compatibility"), env)
                elif _installed_satisfies(adapter_pins):
                    mode, observation = "in-process", run_in_process(probe_source("compatibility"), env)
                else:
                    observation = None
                    unknowns.append("pinned-owners-not-installed-here:pass-owner-image")
            except ProbeError as error:
                observation = None
                compatibility["probe_error"] = str(error)[:300]
                unknowns.append("compatibility-probe-failed")
            compatibility["mode"] = mode
            if observation is not None:
                compatibility["pins_satisfied"] = observation.get("pins_satisfied")
                compatibility["installed"] = observation.get("installed")
                compatibility["domains"] = {
                    domain: _domain_summary(value) for domain, value in (observation.get("domains") or {}).items()
                }
    restore["cluster_torn_down"] = not keep
    domain_states = compatibility.get("domains") or {}
    if restore.get("pg_restore_exit") != 0 or restore.get("setup_error"):
        status = "error"
    elif not domain_states or compatibility.get("pins_satisfied") is False:
        status = "error"
    elif all(domain_states.get(domain, {}).get("compatible") for domain in domains):
        status = "ok"
    else:
        status = "attention"
    compatibility["state"] = (
        "compatible" if status == "ok" else "incompatible" if status == "attention" else "unknown")
    return DiagnosticResult("isolated-restore", "1", status,
                            {"restore": restore, "compatibility": compatibility, "live_database_touched": False},
                            work_refs=refs, unknowns=unknowns)


def _domain_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    handshake = value.get("handshake") or {}
    summary: dict[str, Any] = {"compatible": bool(value.get("compatible")), "reason": value.get("reason")}
    if "remote_schema" in handshake:
        summary["schema"] = handshake["remote_schema"]
    elif "installed_schema_version" in handshake:
        summary["schema"] = {"actual": handshake.get("installed_schema_version"),
                             "minimum": handshake.get("minimum_schema_version"),
                             "maximum": handshake.get("maximum_schema_version")}
    if "error" in value:
        summary["error"] = value["error"]
    return summary
