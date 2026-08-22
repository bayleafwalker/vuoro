"""Kctl `knowledge/v1` behind the uniform construction protocol."""

from __future__ import annotations

from typing import Any


def build(runtime: Any) -> Any:
    from kctl.application import CentralKnowledgeApplication

    return CentralKnowledgeApplication(
        schema=runtime.require("schema"),
        connection_factory=_connection_factory(runtime.require("dsn")),
        expected_environment_name=runtime.environment_name,
        expected_environment_class=runtime.environment_class,
    )


def register(registry: Any, application: Any) -> None:
    from kctl.vuoro import register_operations

    register_operations(registry, application=application)


def _connection_factory(dsn: str):
    def factory():
        import psycopg

        return psycopg.connect(dsn)

    return factory
