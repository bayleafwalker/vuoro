"""Auditctl `audit/v1` behind the uniform construction protocol.

This is the shim that pays for the protocol. Auditctl registers through an
instance method rather than a module-level function, and v3 handled that with a
hardcoded exception in service code: a string comparison against
``auditctl.vuoro_adapter`` / ``VuoroAuditAdapter.register`` that bypassed the
shared loader entirely. Here the difference is absorbed where it belongs -- the
adapter's own ``register`` takes ``(registry, application)`` like every other,
and the composer has no idea an instance method is involved.
"""

from __future__ import annotations

from typing import Any


def build(runtime: Any) -> Any:
    from auditctl.vuoro_adapter import VuoroAuditAdapter

    return VuoroAuditAdapter(
        connection_factory=_connection_factory(runtime.require("dsn")),
        schema=runtime.require("schema"),
    )


def register(registry: Any, application: Any) -> None:
    application.register(registry)


def _connection_factory(dsn: str):
    def factory():
        import psycopg

        return psycopg.connect(dsn)

    return factory
