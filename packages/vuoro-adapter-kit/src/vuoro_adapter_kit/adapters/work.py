"""Sprintctl `work-api/v1` behind the uniform construction protocol.

The work application is re-scoped per invocation by the owner, so what is built
here is the template instance v3 also builds: one connection, one seeded
repository id, and the owner's own constructor.

Project-scoped composition -- Vuoro's project bindings and the authorization
bridge around them -- is deliberately absent. It is Vuoro-side authority
configuration rather than owner construction, and folding it into a shim would
put a Vuoro policy decision inside the translation layer.
"""

from __future__ import annotations

from typing import Any


def build(runtime: Any) -> Any:
    from sprintctl import pg
    from sprintctl.application import WorkApplication

    store = pg.get_connection(runtime.require("dsn"))
    store.repo_id = runtime.require("repository_id")
    return WorkApplication.postgres(store)


def register(registry: Any, application: Any) -> None:
    from sprintctl.vuoro_adapter import register_work_catalog

    register_work_catalog(registry, application)
