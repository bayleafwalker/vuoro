"""Isolated restore: a pg_dump into a throwaway PostgreSQL 16, then the pinned check.

The operation accepts a dump *file* and never a connection string.  It
initializes its own cluster in a fresh temporary directory that listens on a
Unix socket only (``listen_addresses=''``) inside a 0700 directory, so no
other user and no network peer can reach it, and a live database is
unreachable by construction.

The dump is restored by a non-superuser restore role that is a member of the
roles the dump names.  Statements that need superuser -- ``COPY ... TO
PROGRAM``, untrusted procedural languages, most extensions -- therefore fail
the restore (``--exit-on-error --single-transaction``) instead of executing on
this host.  Even so, only restore dumps from a known producer: pg_restore's
own parsing is exposed to the archive.

The pinned owners' read-only startup checks then run as each domain's runtime
role against the restored state, and the cluster is deleted.
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
import subprocess
import tempfile
from typing import Any, Iterator
from urllib.parse import quote

from .result import DiagnosticResult, load_pins
from .runtime import ImageRunner, ProbeError, probe_source, run_in_image, run_in_process

_NIX_PG16 = Path("/nix/store/lkcp4yn4dmkhmngwa363wwqyhhk5rzs4-postgresql-16.15/bin")
REQUIRED_MAJOR = 16
_ADMIN = "vuoro_ops_admin"
_RESTORER = "vuoro_ops_restorer"
_DATABASE = "vuoro_restore"
_PORT = 5432  # names the socket file only; nothing listens on TCP
_IMAGE_SOCKET_DIR = "/run/vuoro-ops-pg"
_AGE_MAGIC = b"age-encryption.org/"
_CUSTOM_MAGIC = b"PGDMP"
_IDENT = r'(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_$]*))'
_ROLE_PATTERNS = (
    re.compile(r"OWNER TO " + _IDENT),
    re.compile(r"FOR ROLE " + _IDENT),
)
_GRANT = re.compile(r"\b(?:GRANT|REVOKE)\b[^;]*?\b(?:TO|FROM) ([^;]+);")
_RESERVED_ROLES = {"PUBLIC", "CURRENT_USER", "SESSION_USER", "CURRENT_ROLE", _ADMIN, _RESTORER}

ProbeRunner = Callable[[str, Mapping[str, str]], dict[str, Any]]


class RestoreRefused(ValueError):
    """The input or environment is not something this operation restores with."""


def _major(bin_dir: Path) -> int | None:
    try:
        completed = subprocess.run([str(bin_dir / "initdb"), "--version"], capture_output=True, text=True,
                                   timeout=30, check=False)
    except OSError:
        return None
    match = re.search(r"\(PostgreSQL\) (\d+)", completed.stdout)
    return int(match.group(1)) if match else None


def pg_bin_dir(explicit: Path | None = None) -> Path:
    """A PostgreSQL 16 bin directory; an explicit non-16 directory is refused."""
    configured = explicit or (Path(os.environ["VUORO_PG_BIN"]) if os.environ.get("VUORO_PG_BIN") else None)
    if configured is not None:
        if _major(configured) != REQUIRED_MAJOR:
            raise RestoreRefused(f"{configured} is not PostgreSQL {REQUIRED_MAJOR} (production runs CNPG 16)")
        return configured
    found = shutil.which("initdb")
    candidates = [Path(found).parent if found else None, _NIX_PG16, Path(f"/usr/lib/postgresql/{REQUIRED_MAJOR}/bin")]
    for candidate in candidates:
        if (candidate is not None and (candidate / "initdb").is_file() and (candidate / "pg_restore").is_file()
                and _major(candidate) == REQUIRED_MAJOR):
            return candidate
    raise RestoreRefused(f"no PostgreSQL {REQUIRED_MAJOR} binaries (initdb, pg_ctl, pg_restore) found; set VUORO_PG_BIN")


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=600, **kwargs)


def _socket_parent() -> str | None:
    # Unix socket paths are limited to ~107 bytes; prefer a short /tmp path.
    return "/tmp" if os.path.isdir("/tmp") and os.access("/tmp", os.W_OK) else None


@contextmanager
def disposable_cluster(bin_dir: Path, *, keep: bool = False) -> Iterator[dict[str, Any]]:
    """A trust-auth PostgreSQL reachable only through a socket in a 0700 directory."""
    root = Path(tempfile.mkdtemp(prefix="vuoro-ops-restore-"))
    socket_dir = Path(tempfile.mkdtemp(prefix="vops-pg-", dir=_socket_parent()))  # created 0700
    data = root / "data"
    init = _run([str(bin_dir / "initdb"), "-D", str(data), "-U", _ADMIN, "-A", "trust",
                 "-E", "UTF8", "--locale=C", "--no-sync"])
    if init.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(socket_dir, ignore_errors=True)
        raise RestoreRefused(f"initdb failed: {init.stderr.strip()[-300:]}")
    options = (f"-c listen_addresses='' -c port={_PORT} -c unix_socket_directories={socket_dir} "
               "-c unix_socket_permissions=0700 -c fsync=off -c synchronous_commit=off -c full_page_writes=off")
    start = _run([str(bin_dir / "pg_ctl"), "-D", str(data), "-o", options, "-l", str(root / "server.log"),
                  "-w", "-t", "60", "start"])
    if start.returncode != 0:
        log = (root / "server.log").read_text(errors="replace")[-400:] if (root / "server.log").exists() else ""
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(socket_dir, ignore_errors=True)
        raise RestoreRefused(f"disposable PostgreSQL did not start: {log}")
    cluster = {"root": str(root), "socket_dir": str(socket_dir), "port": _PORT, "admin": _ADMIN,
               "server_version": _run([str(bin_dir / "postgres"), "--version"]).stdout.strip()}
    try:
        yield cluster
    finally:
        _run([str(bin_dir / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"])
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(socket_dir, ignore_errors=True)
        cluster["torn_down"] = not keep


def _psql(bin_dir: Path, cluster: Mapping[str, Any], sql: str, database: str = "postgres") -> subprocess.CompletedProcess:
    return _run([str(bin_dir / "psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", cluster["socket_dir"],
                 "-p", str(cluster["port"]), "-U", _ADMIN, "-d", database, "-At", "-c", sql])


def _ident(match: re.Match, first: int = 1) -> str:
    quoted, plain = match.group(first), match.group(first + 1)
    return quoted.replace('""', '"') if quoted is not None else plain


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
    return sorted(role for role in roles if role not in _RESERVED_ROLES and not role.startswith("pg_"))


def object_owners(schema_sql: str) -> list[str]:
    return sorted({_ident(match) for match in _ROLE_PATTERNS[0].finditer(schema_sql)} - _RESERVED_ROLES)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _dsn(socket_dir: str, role: str, schema: str | None) -> str:
    query = f"host={quote(socket_dir, safe='')}&port={_PORT}"
    if schema:
        query += "&options=" + quote(f"-csearch_path={schema}", safe="")
    return f"postgresql://{quote(role, safe='')}@/{_DATABASE}?{query}"


def _installed_satisfies(pins: Mapping[str, str]) -> bool:
    try:
        return all(version(name) == expected for name, expected in pins.items()) and bool(
            importlib.util.find_spec("psycopg"))
    except PackageNotFoundError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    if dump_path.is_symlink() or not dump_path.is_file():
        return DiagnosticResult("isolated-restore", "1", "error", {"dump": str(dump_path), "refused": "dump must be a regular file"})
    with dump_path.open("rb") as handle:
        head = handle.read(32)
    if head.startswith(_AGE_MAGIC):
        return DiagnosticResult("isolated-restore", "1", "error", {
            "dump": str(dump_path), "refused": "age-encrypted dumps are out of scope; decrypt to a pg_dump -Fc file first"})
    if not head.startswith(_CUSTOM_MAGIC):
        return DiagnosticResult("isolated-restore", "1", "error", {
            "dump": str(dump_path), "refused": "not a pg_dump custom-format (-Fc) archive"})
    digest = _sha256_file(dump_path)
    refs.append({"kind": "dump", "id": f"sha256:{digest}"})
    bins = pg_bin_dir(bin_dir)
    pins_document = load_pins(manifest_path)
    adapter_pins = {
        lock["distribution"]: lock["distribution_version"]
        for lock in pins_document.get("release_locks", [])
        if lock.get("lock_kind") in ("adapter", "shared-dependency", "owner-dependency")
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
    # Runtime roles never receive CREATE here, even if the dump makes them
    # owners of public-schema objects: the restore then fails rather than
    # handing a runtime role DDL privileges production does not give it.
    runtime_roles = {(roles or {}).get(domain, f"{domain}_runtime") for domain in domains}
    public_owners = [role for role in object_owners(schema_sql.stdout)
                     if role in needed_roles and role not in runtime_roles]

    restore: dict[str, Any] = {
        "dump": str(dump_path), "dump_sha256": digest, "format": "custom",
        "toc_entries": len(toc_entries), "schemas": dump_schemas, "roles_created": needed_roles,
        "restored_as": _RESTORER, "restorer_superuser": False,
        "public_schema_create_granted_to_object_owners": public_owners,
        "create_withheld_from_runtime_roles": sorted(runtime_roles),
    }
    compatibility: dict[str, Any] = {}
    with disposable_cluster(bins, keep=keep) as cluster:
        restore["server_version"] = cluster["server_version"]
        statements = [f"CREATE ROLE {_quote_ident(role)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                      for role in needed_roles]
        statements.append(f"CREATE ROLE {_RESTORER} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        statements += [f"GRANT {_quote_ident(role)} TO {_RESTORER}" for role in needed_roles]
        statements.append(f"CREATE DATABASE {_DATABASE} OWNER {_RESTORER}")
        # ALTER SCHEMA ... OWNER TO needs CREATE on the database only for the
        # restorer (the database owner).  ALTER <object> ... OWNER TO needs the
        # new owner to hold CREATE on the containing schema; the public schema
        # belongs to the database owner (the restorer) here.
        in_database = [f"GRANT CREATE ON SCHEMA public TO {_quote_ident(role)}" for role in public_owners]
        for statement, database in [(sql, "postgres") for sql in statements] + [(sql, _DATABASE) for sql in in_database]:
            created = _psql(bins, cluster, statement, database)
            if created.returncode != 0:
                restore["setup_error"] = created.stderr.strip()[-300:]
                break
        applied = _run([str(bins / "pg_restore"), "-h", cluster["socket_dir"], "-p", str(cluster["port"]),
                        "-U", _RESTORER, "-d", _DATABASE, "--exit-on-error", "--single-transaction",
                        "--no-tablespaces", str(dump_path)])
        restore["pg_restore_exit"] = applied.returncode
        if applied.returncode != 0:
            restore["pg_restore_error"] = applied.stderr.strip()[-600:]
        restored_schemas = _psql(bins, cluster, "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
                                 "AND nspname <> 'information_schema' ORDER BY 1", _DATABASE)
        restore["restored_schemas"] = restored_schemas.stdout.split()

        if applied.returncode == 0 and not restore.get("setup_error"):
            in_image = probe_runner is None and owner_image is not None
            socket_dir = _IMAGE_SOCKET_DIR if in_image else cluster["socket_dir"]
            env = {"VUORO_OPS_PINS": json.dumps(adapter_pins, sort_keys=True)}
            used_roles: dict[str, str | None] = {}
            for domain in domains:
                role = (roles or {}).get(domain, f"{domain}_runtime")
                schema = (schemas or {}).get(domain, domain)
                if role not in needed_roles:
                    # Never substitute a superuser: the check would prove nothing
                    # about the runtime role's privileges.
                    unknowns.append(f"runtime-role-absent-from-dump:{domain}:{role}")
                    used_roles[domain] = None
                    continue
                used_roles[domain] = role
                env[f"VUORO_OPS_{domain.upper()}_DSN"] = _dsn(socket_dir, role, schema)
                env[f"VUORO_OPS_{domain.upper()}_SCHEMA"] = schema
            compatibility["roles"] = used_roles
            mode = None
            observation = None
            try:
                if probe_runner is not None:
                    mode, observation = "injected", probe_runner(probe_source("compatibility"), env)
                elif owner_image is not None:
                    refs.append({"kind": "owner-image", "id": owner_image})
                    runner = image_runner or run_in_image
                    mode, observation = "image", runner(
                        owner_image, probe_source("compatibility"), env,
                        mounts={cluster["socket_dir"]: _IMAGE_SOCKET_DIR}, as_invoking_user=True)
                elif _installed_satisfies(adapter_pins):
                    mode, observation = "in-process", run_in_process(probe_source("compatibility"), env)
                else:
                    unknowns.append("pinned-owners-not-installed-here:pass-owner-image")
            except ProbeError as error:
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
    unchecked = [domain for domain in domains if domain not in domain_states]
    for domain in unchecked:
        unknowns.append(f"domain-not-checked:{domain}")
    if restore.get("pg_restore_exit") != 0 or restore.get("setup_error"):
        status = "error"
    elif not domain_states or compatibility.get("pins_satisfied") is False:
        status = "error"
    elif any(not state.get("compatible") for state in domain_states.values()):
        status = "attention"
    elif unchecked:
        status = "attention"
    else:
        status = "ok"
    compatibility["state"] = (
        "compatible" if status == "ok"
        else "incompatible" if any(not state.get("compatible") for state in domain_states.values())
        else "unknown")
    return DiagnosticResult("isolated-restore", "1", status,
                            {"restore": restore, "compatibility": compatibility, "live_database_touched": False},
                            work_refs=refs, unknowns=list(dict.fromkeys(unknowns)))


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
