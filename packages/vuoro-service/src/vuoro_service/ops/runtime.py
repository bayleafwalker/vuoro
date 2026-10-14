"""Running a probe inside a pinned interpreter: this process or an image.

A probe is one self-contained Python script (stdlib plus whatever the pinned
image ships) that prints one JSON object.  The same text runs in-process when
the pinned distributions are installed here, or inside an immutable service
image through the local container engine.  Images run with ``--rm``, no
volumes, no network unless handed a loopback DSN, the image's own non-root
user and a read-only root filesystem.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib.resources import files
import json
import os
import shutil
import ssl
import subprocess
from typing import Any

ImageRunner = Callable[[str, str, Mapping[str, str]], dict[str, Any]]


def tls_context() -> ssl.SSLContext:
    """Verified TLS using the first trust store that exists.

    ``SSL_CERT_FILE``, then certifi, then the distribution bundle; a
    standalone interpreter's compiled-in default may point nowhere.
    """
    candidates = [os.environ.get("SSL_CERT_FILE"), os.environ.get("NIX_SSL_CERT_FILE")]
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates += ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


class ProbeError(RuntimeError):
    """A probe could not produce its JSON observation."""


def probe_source(name: str) -> str:
    return files("vuoro_service.ops").joinpath(f"probes/{name}.py").read_text(encoding="utf-8")


def container_engine() -> str | None:
    for engine in ("podman", "docker"):
        path = shutil.which(engine)
        if path:
            return path
    return None


def run_in_image(
    image: str, script: str, env: Mapping[str, str], *, timeout: float = 300.0
) -> dict[str, Any]:
    engine = container_engine()
    if engine is None:
        raise ProbeError("no container engine (podman or docker) is available")
    # Only a probe handed a (disposable, loopback) DSN needs the host network.
    network = "host" if any(key.endswith("_DSN") for key in env) else "none"
    argv = [engine, "run", "--rm", "-i", "--network", network, "--read-only",
            "--tmpfs", "/tmp", "--entrypoint", "python"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    argv += [image, "-I", "-"]
    try:
        completed = subprocess.run(
            argv, input=script, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise ProbeError(f"image probe timed out after {timeout:.0f}s") from error
    return _parse(completed.stdout, completed.returncode, completed.stderr)


def run_in_process(script: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Execute a probe in this interpreter with ``env`` as its environment.

    The probe reads ``PROBE_ENV`` instead of ``os.environ`` so an in-process
    run never mutates this process's environment.
    """
    namespace: dict[str, Any] = {"__name__": "vuoro_ops_probe", "PROBE_ENV": dict(env)}
    exec(compile(script, "<vuoro-ops-probe>", "exec"), namespace)  # noqa: S102 - packaged probe text
    observation = namespace["observe"](dict(env))
    if not isinstance(observation, dict):
        raise ProbeError("probe returned a non-object observation")
    return observation


def _parse(stdout: str, returncode: int, stderr: str) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ProbeError(
            f"probe produced no output (exit {returncode}): {stderr.strip()[-400:]}"
        )
    try:
        observation = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise ProbeError(f"probe output is not JSON (exit {returncode})") from error
    if not isinstance(observation, dict):
        raise ProbeError("probe returned a non-object observation")
    return observation
