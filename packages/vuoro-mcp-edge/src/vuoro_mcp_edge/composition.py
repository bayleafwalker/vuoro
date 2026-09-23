"""Environment-driven construction of the MCP protocol server.

`create_app_from_environment` is the uvicorn factory `vuoro-service
mcp-serve` runs.  Configuration is the runtime shell's own gateway-assertion
trust configuration (read through
`vuoro_service.composition.load_gateway_assertion_resolver`, so both
processes trust exactly the same key, issuer, audience, key id, workspace and
project binding) plus the upstream base URL.

The process refuses to start if its environment carries a credential: a
`vuo_pat_` workspace token in any variable, or any `*_DSN` variable.  It has
no use for either, so their presence means a deployment shared the shell's
Secret with this container, which is the exposure this design avoids.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import FastAPI
from vuoro_service.composition import CompositionError, load_gateway_assertion_resolver

from .server import create_edge_app
from .work_source import DEFAULT_UPSTREAM_URL, ShellWorkSource

__all__ = [
    "ENV_UPSTREAM_TIMEOUT",
    "ENV_UPSTREAM_URL",
    "EdgeConfigurationError",
    "create_app_from_environment",
    "refuse_credentials",
]

ENV_UPSTREAM_URL = "VUORO_MCP_UPSTREAM_URL"
ENV_UPSTREAM_TIMEOUT = "VUORO_MCP_UPSTREAM_TIMEOUT_SECONDS"
_TOKEN_PREFIX = "vuo_pat_"


class EdgeConfigurationError(RuntimeError):
    """The environment cannot start this server safely."""


def refuse_credentials(env: Mapping[str, str]) -> None:
    """Refuse an environment that hands this process any credential."""

    offending = sorted(
        name
        for name, value in env.items()
        if name.upper().endswith("_DSN") or value.strip().startswith(_TOKEN_PREFIX)
    )
    if offending:
        raise EdgeConfigurationError(
            "the MCP server holds no credential, but its environment carries "
            f"one in: {', '.join(offending)}"
        )


def _upstream_url(env: Mapping[str, str]) -> str:
    value = env.get(ENV_UPSTREAM_URL, DEFAULT_UPSTREAM_URL).strip()
    if not value.startswith(("http://", "https://")):
        raise EdgeConfigurationError(f"{ENV_UPSTREAM_URL} must be an http(s) URL")
    return value


def _timeout(env: Mapping[str, str]) -> float:
    raw = env.get(ENV_UPSTREAM_TIMEOUT, "5").strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise EdgeConfigurationError(f"{ENV_UPSTREAM_TIMEOUT} must be a number") from error
    if not 0 < value <= 30:
        raise EdgeConfigurationError(f"{ENV_UPSTREAM_TIMEOUT} must be in (0, 30]")
    return value


def create_app_from_environment(env: Mapping[str, str] | None = None) -> FastAPI:
    env = os.environ if env is None else env
    refuse_credentials(env)
    try:
        resolver = load_gateway_assertion_resolver(env)
    except CompositionError as error:
        raise EdgeConfigurationError(str(error)) from error
    work_source = ShellWorkSource(base_url=_upstream_url(env), request_timeout=_timeout(env))
    return create_edge_app(identity_resolver=resolver, work_source=work_source)
