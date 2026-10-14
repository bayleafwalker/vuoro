"""Run the pinned owners' read-only startup compatibility checks.

The same calls ``create_composed_app`` makes at startup: Sprintctl's
``compatibility_handshake`` on the work store and Auditctl's
``check_compatibility`` for the runtime role.  Neither migrates.  Callers
must point the DSNs at a disposable database only.  Output: one JSON object.
"""

import json
import os


def _version(name):
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def observe(env):
    pins = json.loads(env.get("VUORO_OPS_PINS") or "{}")
    installed = {name: _version(name) for name in pins}
    observation = {
        "schema_version": "vuoro-ops-compatibility/v1",
        "pinned": pins,
        "installed": installed,
        "pins_satisfied": all(installed[name] == pins[name] for name in pins),
        "domains": {},
    }
    if not observation["pins_satisfied"]:
        return observation
    work_dsn = env.get("VUORO_OPS_WORK_DSN")
    if work_dsn:
        try:
            from sprintctl import pg as work_pg
            from sprintctl import pg_migrations as work_migrations

            store = work_pg.get_connection(work_dsn)
            try:
                handshake = work_migrations.compatibility_handshake(store)
            finally:
                close = getattr(store.conn, "close", None)
                if close is not None:
                    close()
            observation["domains"]["work"] = {
                "compatible": bool(handshake.get("compatible")),
                "reason": handshake.get("reason"),
                "handshake": _jsonable(handshake),
            }
        except Exception as error:  # noqa: BLE001 - observation, never raise
            observation["domains"]["work"] = {
                "compatible": False,
                "reason": "probe-error",
                "error": f"{type(error).__name__}: {str(error)[:300]}",
            }
    audit_dsn = env.get("VUORO_OPS_AUDIT_DSN")
    if audit_dsn:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from auditctl.vuoro_adapter import VuoroAuditAdapter

            adapter = VuoroAuditAdapter(
                connection_factory=lambda: psycopg.connect(audit_dsn, row_factory=dict_row),
                schema=env.get("VUORO_OPS_AUDIT_SCHEMA", "audit"),
            )
            record = adapter.compatibility()
            observation["domains"]["audit"] = {
                "compatible": bool(record.get("compatible")),
                "reason": ",".join(record.get("reasons") or ()) or None,
                "handshake": _jsonable(record),
            }
        except Exception as error:  # noqa: BLE001
            observation["domains"]["audit"] = {
                "compatible": False,
                "reason": "probe-error",
                "error": f"{type(error).__name__}: {str(error)[:300]}",
            }
    return observation


if __name__ == "__main__":
    print(json.dumps(observe(dict(os.environ)), sort_keys=True))
