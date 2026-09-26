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

from . import claim_tools, effect_tools, record_tools
from .runs import UnavailableRunRegistry
from .server import create_edge_app
from .toolsets import ToolSet, ToolsetContext
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
#: Named here so a missing one is reported as this server's requirement; the
#: shell's loader then validates every value exactly as the shell does.
_REQUIRED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("VUORO_ENVIRONMENT_NAME", ("VUORO_ENVIRONMENT",)),
    ("VUORO_WORKSPACE_ID", ()),
    ("VUORO_GATEWAY_PUBLIC_KEY_FILE", ()),
    ("VUORO_GATEWAY_ASSERTION_ISSUER", ()),
)


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
    for name, aliases in _REQUIRED:
        if not any(env.get(candidate, "").strip() for candidate in (name, *aliases)):
            raise EdgeConfigurationError(f"{name} is required for the MCP edge")
    try:
        resolver = load_gateway_assertion_resolver(env)
    except CompositionError as error:
        raise EdgeConfigurationError(str(error)) from error
    work_source = ShellWorkSource(base_url=_upstream_url(env), request_timeout=_timeout(env))
    context = ToolsetContext(env=env, work_source=work_source, runs=UnavailableRunRegistry())
    return create_edge_app(
        identity_resolver=resolver,
        work_source=work_source,
        toolsets=build_toolsets(context),
    )


def build_toolsets(context: ToolsetContext) -> tuple[ToolSet, ...]:
    """E2's record and claim toolsets, then E3's effect toolset.

    Each builder returns None until its work item ships tools; the order here
    is the tool order clients see after the built-in read tools.
    """

    built = (
        record_tools.build_toolset(context),
        claim_tools.build_toolset(context),
        effect_tools.build_toolset(context),
    )
    return tuple(toolset for toolset in built if toolset is not None)
