"""Binding to sprintctl served work.agent.* and work.message.* operations.

Enabled only when INBOX_CONFORMANCE_PG_URL (or --inbox-pg-url) names a loopback
disposable database `inbox_conformance*` AND the installed sprintctl serves the
operations; otherwise the suite skips with the reason from unavailable_reason().
Every contract call goes through WorkApplication.invoke; only setup, the clock
shift and the held-commit hook use the disposable database or connection.

Clock: sprintctl.pg_testing.freeze_inbox_clock / shift_inbox_clock set the
session setting `sprintctl.inbox_test_now` on a connection (the caller commits).
Every scope connection is frozen when it opens, so exact-second freshness checks
hold; advance_clock shifts every open scope. Without those functions the CLOCK
capability is absent and clock-dependent tests skip.
Restart reconnects the pool and application. The concurrent hook gates the
sending connection's commit() so transaction A stays open while B runs, and
sender B uses its own connection, never the reader's.
Owner stamps carry tag-prefixed workspace_id/repo_id; results are mapped back to
the test's Ctx values.
"""
import re
import threading
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

from .inbox_contract import (CLOCK, CONCURRENT_COMMIT_ORDER, RESTART, ContractRefusal, Ctx,
                             ReversedSends)

OPERATIONS = ("work.agent.register-v1", "work.agent.observe-v1", "work.agent.retire-v1",
              "work.agent.list-v1", "work.agent.describe-v1", "work.message.send-v1",
              "work.message.read-v1", "work.message.status-v1", "work.message.receipt-v1",
              "work.message.cancel-v1")
_UNSUPPORTED = ("unknown-operation", "operation-not-found", "unsupported-operation", "not-implemented")


def _check_url(url):
    parsed = urlsplit(url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("inbox conformance requires a loopback disposable PostgreSQL database")
    if not parsed.path.startswith("/inbox_conformance"):
        raise ValueError("database must be inbox_conformance*")


def unavailable_reason(url):
    """None when the binding can run, else the human-readable skip reason."""
    if not url:
        return "INBOX_CONFORMANCE_PG_URL is not set"
    try:
        _check_url(url)
        from sprintctl import pg
        from sprintctl.application import ApplicationRejection, WorkApplication
    except ImportError as error:
        return f"sprintctl is not importable: {error}"
    except ValueError as error:
        return str(error)
    probe_ctx = _context(Ctx("probe", "probe", "github:1:0"))
    store = None
    try:
        store = pg.get_connection(url)
        store.repo_id = "inbox-probe-" + uuid.uuid4().hex
        pg.init_db(store)
        app = WorkApplication.postgres(store)
        for operation in OPERATIONS:
            args = {"principal_id": "github:1:0"} if operation.endswith("describe-v1") else {}
            try:
                app.invoke(operation, args, probe_ctx)
            except ApplicationRejection as error:
                if error.code in _UNSUPPORTED:
                    return f"installed sprintctl does not serve {operation} yet"
            except (LookupError, NotImplementedError, AttributeError):
                return f"installed sprintctl does not serve {operation} yet"
    except Exception as error:  # a missing or unreachable database is a skip, not a pass
        return f"disposable PostgreSQL is unusable: {error}"
    finally:
        if store is not None:
            store.conn.close()
    return None


def _context(ctx):
    return SimpleNamespace(
        identity=SimpleNamespace(
            actor=ctx.principal_id, environment="inbox-conformance",
            authorities=frozenset({"work:evidence", "work:claim", "work:read"}),
            principal_id=ctx.principal_id, workspace_id=ctx.workspace_id, client_id=None, grant_id=None),
        request_id="req-" + uuid.uuid4().hex, basis_revision=None, catalog_revision="contract",
        idempotency_requirement="not-allowed", idempotency_key=None)


class _GatedConnection:
    """Delegates to a psycopg connection but holds commit() until released."""
    def __init__(self, conn, reached, release):
        self._conn, self._reached, self._release = conn, reached, release

    def commit(self):
        self._reached.set()
        if not self._release.wait(30):
            raise TimeoutError("held commit was never released")
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


class SprintctlInboxProvider:
    capabilities = frozenset({RESTART, CONCURRENT_COMMIT_ORDER})

    def __init__(self, url):
        _check_url(url)
        from sprintctl import pg
        from sprintctl.application import ApplicationRejection
        self.url, self.pg, self.rejection = url, pg, ApplicationRejection
        self.tag = "inbox-conformance-" + uuid.uuid4().hex
        self.scopes = {}
        self._sprint = {}
        try:
            from sprintctl import pg_testing
        except ImportError:
            pg_testing = None
        self.testing = pg_testing
        if pg_testing is not None and hasattr(pg_testing, "shift_inbox_clock"):
            self.capabilities = self.capabilities | {CLOCK}
        self._mark_disposable()
        self._open_scope("repo-a")  # creates the schema and the first repository

    # -- plumbing ----------------------------------------------------------
    def _mark_disposable(self):
        """The owner honours its test clock only on a database commented as disposable."""
        name = urlsplit(self.url).path.lstrip("/")
        if not re.fullmatch(r"inbox_conformance[A-Za-z0-9_]*", name):
            raise ValueError("database must be inbox_conformance*")
        store = self.pg.get_connection(self.url)
        try:
            with store.conn.cursor() as cursor:
                cursor.execute(f"COMMENT ON DATABASE {name} IS 'sprintctl:disposable-integration-test'")
            store.conn.commit()
        finally:
            store.conn.close()

    def _repo(self, repo_id):
        return f"{self.tag}-{repo_id}"

    def _open_scope(self, repo_id):
        from sprintctl.application import WorkApplication
        if repo_id not in self.scopes:
            store = self.pg.get_connection(self.url)
            store.repo_id = self._repo(repo_id)
            self.pg.init_db(store)
            self._freeze(store)
            app = WorkApplication.postgres(store)
            sprint = self.pg.create_sprint(store, "Inbox conformance", "Contract", "2026-01-01", "2026-12-31", "active")
            track = self.pg.get_or_create_track(store, sprint, "contract")
            self.scopes[repo_id] = (store, app, sprint, track)
        return self.scopes[repo_id]

    def _freeze(self, store):
        if CLOCK in self.capabilities:
            self.testing.freeze_inbox_clock(store)
            store.conn.commit()

    def _extra_connection(self, repo_id):
        """A separate connection to a repository, schema initialized, clock frozen."""
        from sprintctl.application import WorkApplication
        store = self.pg.get_connection(self.url)
        store.repo_id = self._repo(repo_id)
        self.pg.init_db(store)
        self._freeze(store)
        return store, WorkApplication.postgres(store)

    def _plain(self, value):
        """Map tag-prefixed workspace_id/repo_id stamps back to the test's values."""
        prefix = self.tag + "-"
        if isinstance(value, dict):
            return {k: (v[len(prefix):] if k in ("workspace_id", "repo_id") and isinstance(v, str)
                        and v.startswith(prefix) else self._plain(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._plain(v) for v in value]
        return value

    def close(self):
        for store, *_ in self.scopes.values():
            store.conn.close()
        self.scopes.clear()

    def restart(self):
        self.close()
        self._open_scope("repo-a")

    def _invoke(self, ctx, operation, arguments):
        _, app, *_ = self._open_scope(ctx.repo_id)
        scoped = Ctx(f"{self.tag}-{ctx.workspace_id}", ctx.repo_id, ctx.principal_id)
        try:
            return self._plain(app.invoke(operation, arguments, _context(scoped)))
        except self.rejection as error:
            # Owner codes are the contract codes; an unknown code fails the scenario that meets it.
            raise ContractRefusal(error.code, getattr(error, "details", None) or {}) from error

    # -- hooks -------------------------------------------------------------
    def new_item(self, ctx):
        store, _, sprint, track = self._open_scope(ctx.repo_id)
        return int(self.pg.create_work_item(store, sprint, track, "Inbox contract"))

    def new_run(self, ctx):
        return self._invoke(ctx, "work.run.register-v1", {
            "harness_id": "conformance", "harness_build": "test", "model_id": "scripted",
            "recipe_id": "inbox-contract/v1",
            "observed_profile": {"instruction_digest": "sha256:" + "a" * 64, "skill_digests": []},
            "idempotency_key": uuid.uuid4().hex})["run"]["run_id"]

    def advance_clock(self, seconds):
        for store, *_ in self.scopes.values():
            self.testing.shift_inbox_clock(store, seconds)
            store.conn.commit()

    def send_concurrently_reversed(self, sender, message_a, message_b, reader):
        """A starts first and holds its commit; B then tries to commit first.

        With the per-repository counter row B blocks behind A's row lock, so after a
        short wait A is released and commits first. A backend that allocates sequence
        numbers outside that lock lets B commit first, which is what the reader
        polling between the commits then exposes.
        """
        store_a, app_a = self._extra_connection(sender.repo_id)
        store_b, app_b = self._extra_connection(sender.repo_id)
        reached, release, done_a = threading.Event(), threading.Event(), threading.Event()
        store_a.conn = _GatedConnection(store_a.conn, reached, release)
        scoped = Ctx(f"{self.tag}-{sender.workspace_id}", sender.repo_id, sender.principal_id)
        failure = []

        def run_a():
            try:
                app_a.invoke("work.message.send-v1", {"message": message_a}, _context(scoped))
            except Exception as error:  # surfaced after join
                failure.append(error)
            finally:
                done_a.set()
        thread_a = threading.Thread(target=run_a)
        thread_a.start()
        if not reached.wait(30):
            raise RuntimeError("transaction A never reached commit")
        done_b = threading.Event()

        def run_b():
            try:
                app_b.invoke("work.message.send-v1", {"message": message_b}, _context(scoped))
            except Exception as error:
                failure.append(error)
            finally:
                done_b.set()
        thread_b = threading.Thread(target=run_b)
        thread_b.start()
        b_first = done_b.wait(2)
        first = self.read(reader, after_seq=0, limit=100)
        release.set()
        thread_a.join(30)
        thread_b.join(30)
        if failure:
            raise failure[0]
        store_a.conn._conn.close()
        store_b.conn.close()
        committed = [message_b["message_id"], message_a["message_id"]] if b_first else [
            message_a["message_id"], message_b["message_id"]]
        second = self.read(reader, after_seq=first["next_after_seq"], limit=100)
        return ReversedSends(committed, first, second)

    # -- work.agent.* ------------------------------------------------------
    def register(self, ctx, **kw):
        return self._invoke(ctx, "work.agent.register-v1", kw)

    def observe(self, ctx, **kw):
        return self._invoke(ctx, "work.agent.observe-v1", {k: v for k, v in kw.items() if v is not None})

    def retire(self, ctx, **kw):
        return self._invoke(ctx, "work.agent.retire-v1", kw)

    def list(self, ctx):
        return self._invoke(ctx, "work.agent.list-v1", {})

    def describe(self, ctx, **kw):
        return self._invoke(ctx, "work.agent.describe-v1", kw)

    # -- work.message.* ----------------------------------------------------
    def send(self, ctx, **kw):
        return self._invoke(ctx, "work.message.send-v1", kw)

    def read(self, ctx, **kw):
        return self._invoke(ctx, "work.message.read-v1", {k: v for k, v in kw.items() if v is not None})

    def status(self, ctx, **kw):
        return self._invoke(ctx, "work.message.status-v1", kw)

    def receipt(self, ctx, **kw):
        return self._invoke(ctx, "work.message.receipt-v1", kw)

    def cancel(self, ctx, **kw):
        return self._invoke(ctx, "work.message.cancel-v1", kw)
