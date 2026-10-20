"""Read-only prerequisites for the optional, released-owner demo consumer."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


QUALIFIED_VERSIONS = {
    "vuoro-client": "0.1.2",
    "vuoro-service": "0.1.97",
    "sprintctl": "0.22.0",
}


def tool_check(name: str, directory: Path | None = None) -> dict:
    # An explicit directory is authoritative; never fall back to a PATH tool.
    candidate = directory / name if directory is not None else shutil.which(name)
    path = Path(candidate).absolute() if candidate else None
    check = {"name": name, "status": "blocked"}
    if path is None or not path.is_file() or not os.access(path, os.X_OK):
        check["detail"] = ("Install Git and put git on PATH." if name == "git" else
            "Install PostgreSQL 16 tools; pass their directory with --pg-bin.")
        return check
    try:
        result = subprocess.run([str(path), "--version"], check=True,
            capture_output=True, text=True, timeout=5,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C"})
    except (OSError, subprocess.SubprocessError, UnicodeError):
        # No arbitrary exception text, environment values, or tool stderr.
        check["detail"] = "Version probe failed; check the executable and its libraries."
        return check
    pattern = (r"^git version (\d+\.\d+(?:\.\d+)?)(?:\S*)\s*$" if name == "git" else
        rf"^{name} \(PostgreSQL\) (16\.\d+)(?:\s.*)?$")
    match = re.fullmatch(pattern, result.stdout.strip())
    if match is None:
        check["detail"] = ("Expected a Git version response." if name == "git" else
            "This demo requires PostgreSQL 16 for both initdb and pg_ctl.")
        return check
    return {"name": name, "status": "ready", "version": match[1], "path": str(path)}


def postgres_tools(directory: Path | None) -> dict[str, str]:
    """Use the same PG executable/version gate in doctor and actual startup."""
    checks = [tool_check(name, directory) for name in ("initdb", "pg_ctl")]
    if any(check["status"] != "ready" for check in checks):
        raise ValueError("PostgreSQL 16 tools required; run vuoro doctor --pg-bin DIR")
    if os.name != "posix" or os.geteuid() == 0:
        raise ValueError("Run the disposable PostgreSQL fixture as a non-root Unix user")
    return {check["name"]: check["path"] for check in checks}


def diagnose(directory: Path | None) -> dict:
    checks = [{"name": "python", "status": "ready" if sys.version_info >= (3, 12) else "blocked",
        "detail": "Python 3.12 or newer is required."}]
    checks.append({"name": "runtime-user", "status": "ready" if
        os.name == "posix" and os.geteuid() != 0 else "blocked",
        "detail": "Run as a non-root Unix user; PostgreSQL initdb refuses root."})
    for distribution, expected in QUALIFIED_VERSIONS.items():
        try:
            installed = version(distribution)
        except PackageNotFoundError:
            installed = None
        checks.append({"name": distribution,
            "status": "ready" if installed == expected else "blocked",
            "expected": expected, "installed": installed,
            "detail": "Install the demo's declared immutable dependencies in a fresh environment."})
    checks.append(tool_check("git"))
    checks.extend(tool_check(name, directory) for name in ("initdb", "pg_ctl"))
    checks.append({"name": "scratch-directory", "status": "ready" if
        Path("/tmp").is_dir() and os.access("/tmp", os.W_OK | os.X_OK) else "blocked",
        "detail": "The owned fixture needs a writable /tmp for private data and Unix sockets."})
    return {"schema": "vuoro-demo-preflight/v1",
        "status": "ready" if all(check["status"] == "ready" for check in checks) else "blocked",
        "qualification": "Prerequisites only; run vuoro demo for settlement and cleanup evidence.",
        "checks": checks}
