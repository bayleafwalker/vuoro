"""ActionQ `execution/v1` behind the uniform construction protocol.

The wheel-first proof case: an internal, authoritative, frozen contract whose
conformance is proven by operation hashes. What v3 spelled as a literal
``ActionQApplication(...)`` call inside ``create_composed_app`` is spelled here
as a translation from settings the profile pinned.
"""

from __future__ import annotations

from typing import Any


def build(runtime: Any) -> Any:
    from actionq.application import ActionQApplication

    return ActionQApplication(
        schema=runtime.require("schema"),
        connection_factory=_connection_factory(runtime.require("dsn")),
        authorizer=_authorizer,
    )


def register(registry: Any, application: Any) -> None:
    from actionq.vuoro import register_operations

    register_operations(registry, application=application)


def _connection_factory(dsn: str):
    def factory():
        import psycopg

        return psycopg.connect(dsn)

    return factory


def _authorizer(context: Any) -> Any:
    """Identity passthrough, matching what v3's composer supplies.

    Kept as a named function rather than a lambda so a traceback from owner
    code names the shim rather than an anonymous callable.
    """
    return context
