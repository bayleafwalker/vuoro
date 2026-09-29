"""M1-4: the differentiated settlement scenario over the public surface.

agentops#2524.  Two callers, A and B, each with its own lease binding
(principal, workspace, OAuth client, grant), drive the public MCP tools
(`register_run`, `claim_work`, `heartbeat`, `report_outcome`,
`list_ready_work`) while an authority reader records what the work owner
(sprintctl) decided, over `POST /api/invoke/v1`.  Each caller runs as its own
process so "A is killed" is a real SIGKILL, not a skipped call.

Cases:

* takeover -- A claims X and heartbeats; A is killed; B's claim is refused
  (`lease-held`) while A's lease is fresh, then takes the lease over once it
  is stale; A's late `report_outcome` is refused `claim-superseded` and its
  payload stays on X as evidence; B reports under the default verification
  profile `checked` and the authority settles X ("accepted under
  verification profile checked"); Y (blocked by X) is in `next-work`
  `ready_items` and `list_ready_work` only after that settlement.
* restart -- A claims R and heartbeats, is killed, and a new process under the
  same principal, grant and idempotency keys resumes: the same run id, the
  same lease (`resumed: true`), one settlement, no rejected report.
* stale-restart -- as restart, but the new process arrives after the lease
  went stale and nobody took it over: the owner reactivates the same lease
  in place and the report settles.

Modes:

* ``local`` (default; CI): PostgreSQL (``--pg-url``, or a throwaway
  ``postgres:16`` container), the pinned sprintctl wheel's work catalog in the
  real runtime shell (`vuoro_service.app.create_app`) and the real MCP edge
  (`vuoro_mcp_edge`, with the shipped toolsets), each served by uvicorn on
  loopback.  A local key plays the gateway: it mints one assertion per
  request, carrying the caller's grant exactly as the gateway's OAuth path
  does, and the edge forwards it to the shell with an edge proof.  The lease
  TTL is ``--ttl`` (default 30 s, sprintctl's minimum).
* ``live``: ``https://api.vuoro.cloud/mcp`` with two OAuth grants of the
  ``claude-connector`` client (one per caller) and a workspace PAT for the
  authority reader; see docs/evidence/2026-09-29-m1-4-settlement-scenario/.
  The live TTL is the tenant runtime's (600 s unless configured).

The script exits non-zero if any expectation fails, and writes
``transcript.json`` (every tool call and authority read, in order) and
``transcript.md`` (the same, rendered for the evidence packet) to ``--out``.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

SCRIPT = Path(__file__).resolve()
RECIPE_ID = "m1-4-settlement-scenario/v1"
HARNESS_ID = "vuoro-m1-4-scenario"
CLIENT_ID = "claude-connector"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Local gateway stand-in.  Values mirror the hosted gateway's shape.
LOCAL_ISSUER = "vuoro-cloud-control"
LOCAL_AUDIENCE = "vuoro-service"
LOCAL_KEY_ID = "gateway-2026-01"
LOCAL_ENVIRONMENT = "vuoro-cloud-ws-m14local0000"
LOCAL_WORKSPACE_ID = "01M14SCENAR10000000000000W"
LOCAL_REPO_ID = "vuoro"
CALLER_AUTHORITIES = ["work:read", "work:evidence", "work:claim"]
READER_AUTHORITIES = ["work:read", "work:sprint", "work:lifecycle", "work:evidence"]


def ulid() -> str:
    value = int(time.time() * 1000) << 80 | secrets.randbits(80)
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def _principal_ulid(subject: str) -> str:
    """A stable ULID-shaped principal subject per local caller (the gateway's
    `subject` claim is the principal's control-plane user id)."""
    digest = hashlib.sha256(subject.encode()).digest()
    return "01M14" + "".join(CROCKFORD[b & 31] for b in digest[:21])


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path` as a 0600 file, atomically, never wider.

    The temporary file is created 0600 by os.open (no umask window and no
    chmod after the fact) and renamed over `path`.
    """

    tmp = path.with_name(path.name + ".tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def script_digest() -> str:
    return "sha256:" + hashlib.sha256(SCRIPT.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Transcript: one JSONL file every process appends to.
# ---------------------------------------------------------------------------


class Transcript:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, entry: dict[str, Any]) -> None:
        entry = {"at": now_iso(), **entry}
        line = json.dumps(entry, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]


# ---------------------------------------------------------------------------
# Credentials.  An Auth is a callable returning the headers for one request.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalGatewaySpec:
    """What the local gateway stand-in needs to mint one caller's assertions."""

    key_file: str
    subject: str
    grant_id: str | None
    authorities: tuple[str, ...]
    audience: str = LOCAL_AUDIENCE

    def to_arg(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_arg(cls, raw: str) -> "LocalGatewaySpec":
        data = json.loads(raw)
        data["authorities"] = tuple(data["authorities"])
        return cls(**data)


def local_headers(spec: LocalGatewaySpec) -> Callable[[], dict[str, str]]:
    import jwt
    from cryptography.hazmat.primitives import serialization

    private = serialization.load_pem_private_key(Path(spec.key_file).read_bytes(), None)

    def headers() -> dict[str, str]:
        request_id = ulid()
        issued = int(time.time())
        claims: dict[str, Any] = {
            "iss": LOCAL_ISSUER,
            "aud": spec.audience,
            "sub": spec.subject,
            "actor": spec.subject,
            "subject": _principal_ulid(spec.subject),
            "principal_epoch": 0,
            "workspace_id": LOCAL_WORKSPACE_ID,
            "authorities": list(spec.authorities),
            "repo_ids": [LOCAL_REPO_ID],
            "request_id": request_id,
            "jti": "jti-" + secrets.token_hex(12),
            "iat": issued,
            "nbf": issued - 1,
            "exp": issued + 30,
        }
        if spec.grant_id is not None:
            claims["client_id"] = CLIENT_ID
            claims["grant_id"] = spec.grant_id
        token = jwt.encode(
            claims, private, algorithm="EdDSA", headers={"kid": LOCAL_KEY_ID, "typ": "JWT"}
        )
        return {"X-Vuoro-Identity": token, "X-Request-Id": request_id}

    return headers


def oauth_headers(token_file: Path, secret_file: Path) -> Callable[[], dict[str, str]]:
    """Bearer headers from an OAuth grant, refreshing (and rotating) as needed.

    The token file is written by `oauth-login` (0600) and rewritten on every
    refresh, so a restarted caller process keeps the same grant.
    """

    def headers() -> dict[str, str]:
        state = json.loads(token_file.read_text())
        if state["expires_at"] - time.time() < 90:
            secret = secret_file.read_text().strip()
            response = httpx.post(
                state["token_endpoint"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": state["refresh_token"],
                    "resource": state["resource"],
                },
                auth=(CLIENT_ID, secret),
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
            state.update(
                access_token=body["access_token"],
                refresh_token=body.get("refresh_token", state["refresh_token"]),
                expires_at=time.time() + int(body.get("expires_in", 900)),
            )
            write_private(token_file, json.dumps(state))
        return {"Authorization": f"Bearer {state['access_token']}"}

    return headers


def bearer_file_headers(path: Path) -> Callable[[], dict[str, str]]:
    token = path.read_text().strip()
    return lambda: {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Clients: an MCP caller and the authority reader.
# ---------------------------------------------------------------------------


def _is_work_item(value: Any) -> bool:
    return isinstance(value, dict) and (
        "work_id" in value or ("id" in value and "title" in value)
    )


def scope_to_run_items(value: Any, run_items: frozenset[int]) -> Any:
    """What the transcript may keep of an answer: only the run's own items.

    Listings (`list_ready_work`, `next-work` and anything else that returns
    work items) carry every item the caller can see.  On a live workspace
    those are real, unrelated items, and the transcript is committed to a
    public repository, so every list of work items is cut down to the items
    this run created; how many were dropped is kept as a count, never their
    ids or titles.  The expectations are evaluated on the owner's full
    answer, not on this copy.
    """

    if isinstance(value, dict):
        scoped: dict[str, Any] = {}
        omitted: dict[str, int] = {}
        for key, item in value.items():
            if isinstance(item, list) and item and all(_is_work_item(entry) for entry in item):
                kept = [
                    scope_to_run_items(entry, run_items)
                    for entry in item
                    if entry.get("work_id", entry.get("id")) in run_items
                ]
                if len(kept) != len(item):
                    omitted[key] = len(item) - len(kept)
                scoped[key] = kept
            else:
                scoped[key] = scope_to_run_items(item, run_items)
        if omitted:
            scoped["omitted_foreign_items"] = omitted
        return scoped
    if isinstance(value, list):
        return [scope_to_run_items(entry, run_items) for entry in value]
    return value


def scope_detail(detail: Any, run_items: frozenset[int]) -> Any:
    """An expectation's detail as the transcript may keep it.

    A bare list of work ids (a readiness listing reduced to ids) keeps only
    the run's own ids and a count of the rest; anything else goes through
    `scope_to_run_items`.
    """

    if (
        isinstance(detail, list)
        and detail
        and all(isinstance(entry, int) and not isinstance(entry, bool) for entry in detail)
    ):
        kept = [entry for entry in detail if entry in run_items]
        return {"run_items": kept, "omitted_foreign_items": len(detail) - len(kept)}
    return scope_to_run_items(detail, run_items)


class ToolError(Exception):
    def __init__(self, code: str, message: str, content: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.content = content


class McpCaller:
    """One caller on the public MCP route."""

    def __init__(
        self,
        name: str,
        url: str,
        headers: Callable[[], dict[str, str]],
        transcript: Transcript,
        *,
        scope: Callable[[Any], Any] = lambda value: value,
    ) -> None:
        self.scope = scope
        self.name = name
        self.url = url
        self.headers = headers
        self.transcript = transcript
        self.session: str | None = None
        self.client = httpx.Client(timeout=60)
        self._id = 0

    def _rpc(self, method: str, params: dict[str, Any] | None, *, notify: bool = False) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id
        headers = {
            **self.headers(),
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        response = self.client.post(self.url, json=body, headers=headers)
        if response.headers.get("mcp-session-id"):
            self.session = response.headers["mcp-session-id"]
        if notify:
            return None
        if response.status_code >= 400 and "json" not in response.headers.get("content-type", ""):
            raise RuntimeError(f"{method}: HTTP {response.status_code}: {response.text[:300]}")
        payload = _decode_rpc_body(response)
        if "error" in payload:
            raise RuntimeError(f"{method}: JSON-RPC error {payload['error']}")
        return payload["result"]

    def initialize(self) -> None:
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": HARNESS_ID, "version": "1"},
            },
        )
        self._rpc("notifications/initialized", None, notify=True)

    def tools(self) -> list[str]:
        return [tool["name"] for tool in self._rpc("tools/list", {})["tools"]]

    def call(self, tool: str, arguments: dict[str, Any], *, note: str = "") -> dict[str, Any]:
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        content = result.get("structuredContent")
        if content is None:
            texts = [part.get("text", "") for part in result.get("content", [])]
            with contextlib.suppress(ValueError):
                content = json.loads("".join(texts))
            if content is None:
                content = {"text": "".join(texts)}
        is_error = bool(result.get("isError"))
        self.transcript.write(
            {
                "kind": "tool-call",
                "caller": self.name,
                "tool": tool,
                "arguments": arguments,
                "is_error": is_error,
                "result": self.scope(content),
                "note": note,
            }
        )
        if is_error:
            error = content.get("error", content) if isinstance(content, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message", "") if isinstance(error, dict) else str(content)
            raise ToolError(code or "unknown", message, content)
        return content


def _decode_rpc_body(response: httpx.Response) -> dict[str, Any]:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError("empty event stream")
    return response.json()


class AuthorityReader:
    """Reads and writes the work owner's records over `/api/invoke/v1`."""

    def __init__(
        self,
        base_url: str,
        repo_id: str,
        headers: Callable[[], dict[str, str]],
        transcript: Transcript,
        *,
        send_catalog_revision: bool,
        scope: Callable[[Any], Any] = lambda value: value,
    ) -> None:
        self.scope = scope
        self.base_url = base_url.rstrip("/")
        self.repo_id = repo_id
        self.headers = headers
        self.transcript = transcript
        self.client = httpx.Client(timeout=60)
        self.catalog_revision: str | None = None
        if send_catalog_revision:
            response = self.client.get(
                f"{self.base_url}/api/catalog/v1",
                headers={**headers(), "X-Vuoro-Client-Protocol": "1"},
            )
            response.raise_for_status()
            catalog = response.json()
            self.catalog_revision = catalog.get("revision") or catalog.get("catalog_revision")

    def invoke(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        record: bool = True,
        note: str = "",
        idempotency_key: str | None = None,
        basis_revision: str | None = None,
    ) -> dict[str, Any]:
        headers = self.headers()
        request_id = headers.get("X-Request-Id") or f"m14-{uuid.uuid4()}"
        headers["X-Request-Id"] = request_id
        body: dict[str, Any] = {
            "schema_version": "invocation/v1",
            "request_id": request_id,
            "operation": operation,
            "arguments": arguments,
            "repo_id": self.repo_id,
        }
        if self.catalog_revision:
            body["catalog_revision"] = self.catalog_revision
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        if basis_revision:
            body["basis_revision"] = basis_revision
        response = self.client.post(
            f"{self.base_url}/api/invoke/v1",
            json=body,
            headers={**headers, "X-Vuoro-Client-Protocol": "1"},
        )
        envelope = response.json()
        if record:
            self.transcript.write(
                {
                    "kind": "authority",
                    "operation": operation,
                    "arguments": arguments,
                    "status": envelope.get("status"),
                    "result": self.scope(envelope.get("result")),
                    "error": envelope.get("error"),
                    "note": note,
                }
            )
        if envelope.get("status") != "accepted":
            raise RuntimeError(f"{operation} {response.status_code}: {envelope.get('error')}")
        return envelope["result"]

    def ready_ids(self, sprint_id: int, *, note: str = "") -> list[int]:
        result = self.invoke("work.read.next-work", {"sprint_id": sprint_id}, note=note)
        return [item["id"] for item in result.get("ready_items", [])]


# ---------------------------------------------------------------------------
# Worker process: one caller, one action, then exit (or be killed).
# ---------------------------------------------------------------------------


def worker_main(args: argparse.Namespace) -> int:
    transcript = Transcript(Path(args.transcript))
    if args.auth_kind == "local":
        headers = local_headers(LocalGatewaySpec.from_arg(args.auth))
    else:
        token_file, secret_file = args.auth.split(os.pathsep)
        headers = oauth_headers(Path(token_file), Path(secret_file))
    state_path = Path(args.state)
    state = json.loads(state_path.read_text())
    caller = McpCaller(args.name, args.mcp_url, headers, transcript)
    caller.initialize()
    transcript.write({"kind": "process", "caller": args.name, "event": "started",
                      "pid": os.getpid(), "action": args.action})

    def save() -> None:
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(state_path)

    def register() -> str:
        run = caller.call(
            "register_run",
            {
                "harness_id": HARNESS_ID,
                "harness_build": state["harness_build"],
                "model_id": "none:scripted-caller",
                "recipe_id": RECIPE_ID,
                "observed_profile": {
                    "instruction_digest": state["instruction_digest"],
                    "skill_digests": [],
                },
                "idempotency_key": state["run_key"],
            },
        )
        return run["run_id"]

    def claim(run_id: str) -> dict[str, Any]:
        return caller.call(
            "claim_work",
            {"item_id": state["item_id"], "run_id": run_id, "idempotency_key": state["claim_key"]},
        )

    def report(run_id: str, lease_id: str, key: str) -> dict[str, Any]:
        return caller.call(
            "report_outcome",
            {
                "lease_id": lease_id,
                "run_id": run_id,
                "outcome": "succeeded",
                "summary": f"{args.name}: scenario work on item {state['item_id']} finished",
                "payload": {"caller": args.name, "result": state["payload_marker"]},
                "checks": [{"name": "scenario-check", "status": "passed",
                            "ref": f"{RECIPE_ID}#{args.name}"}],
                "idempotency_key": key,
            },
        )

    if args.action == "claim-and-heartbeat":
        run_id = register()
        answer = claim(run_id)
        lease = answer["lease"]
        state.update(run_id=run_id, lease_id=lease["lease_id"],
                     generation=lease.get("generation"), heartbeats=0)
        save()
        interval = min(float(lease["heartbeat_interval_seconds"]), float(args.heartbeat_every))
        while True:  # until the orchestrator kills this process
            time.sleep(interval)
            caller.call("heartbeat", {"lease_id": lease["lease_id"], "run_id": run_id})
            state["heartbeats"] += 1
            save()

    if args.action == "late-report":
        try:
            caller.call("heartbeat", {"lease_id": state["lease_id"], "run_id": state["run_id"]},
                        note="the killed caller comes back and heartbeats its old lease")
        except ToolError as error:
            state["late_heartbeat_code"] = error.code
        else:
            state["late_heartbeat_code"] = None
        try:
            report(state["run_id"], state["lease_id"], state["report_key"])
        except ToolError as error:
            state["late_report_code"] = error.code
        else:
            state["late_report_code"] = None
        save()
        return 0

    if args.action == "resume-and-report":
        run_id = register()
        state["resumed_run_id"] = run_id
        answer = claim(run_id)
        lease = answer["lease"]
        state.update(resumed_lease_id=lease["lease_id"], resumed=answer.get("resumed"))
        caller.call("heartbeat", {"lease_id": lease["lease_id"], "run_id": run_id})
        result = report(run_id, lease["lease_id"], state["report_key"])
        state["settlement_effect"] = result.get("settlement_effect")
        save()
        return 0

    if args.action == "early-claim":
        run_id = register()
        state["run_id"] = run_id
        try:
            claim(run_id)
        except ToolError as error:
            state["early_claim_code"] = error.code
        save()
        return 0

    raise SystemExit(f"unknown action {args.action}")


# ---------------------------------------------------------------------------
# Local stack: PostgreSQL, runtime shell, MCP edge.
# ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def postgres(pg_url: str | None) -> Iterator[str]:
    if pg_url:
        yield pg_url
        return
    port = free_port()
    name = f"m14-scenario-pg-{secrets.token_hex(4)}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=m14-local-only",
         "-e", "POSTGRES_USER=m14", "-e", "POSTGRES_DB=m14_scenario",
         "-p", f"127.0.0.1:{port}:5432", "postgres:16"],
        check=True, capture_output=True,
    )
    url = f"postgresql://m14:m14-local-only@127.0.0.1:{port}/m14_scenario"
    try:
        import psycopg

        deadline = time.time() + 60
        while True:
            try:
                psycopg.connect(url).close()
                break
            except psycopg.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


class _Server(threading.Thread):
    def __init__(self, app: Any, port: int) -> None:
        super().__init__(daemon=True)
        import uvicorn

        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )

    def run(self) -> None:
        self.server.run()

    def wait_started(self) -> None:
        deadline = time.time() + 30
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)


@dataclass
class LocalStack:
    key_file: Path
    shell_url: str
    mcp_url: str
    servers: list[_Server] = field(default_factory=list)

    def stop(self) -> None:
        for server in self.servers:
            server.server.should_exit = True
        for server in self.servers:
            server.join(timeout=10)


def start_local_stack(pg_url: str, key_dir: Path, ttl: int) -> LocalStack:
    os.environ["SPRINTCTL_LEASE_TTL_SECONDS"] = str(ttl)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from sprintctl import pg
    from sprintctl.application import WorkApplication
    from sprintctl.vuoro_adapter import register_work_catalog
    from vuoro_mcp_edge.composition import build_toolsets
    from vuoro_mcp_edge.edge_proof_auth import EdgeProofAuth
    from vuoro_mcp_edge.record_tools import SprintctlRecordStore
    from vuoro_mcp_edge.server import MCP_PATH, create_edge_app
    from vuoro_mcp_edge.toolsets import ToolsetContext
    from vuoro_mcp_edge.work_source import ShellWorkSource
    from vuoro_service.app import ServiceSettings, create_app
    from vuoro_service.catalog import CatalogRegistry
    from vuoro_service.edge_proof import EdgeProofVerifier
    from vuoro_service.gateway_identity import GatewayAssertionIdentityResolver

    private = Ed25519PrivateKey.generate()
    key_file = key_dir / "gateway-private.pem"
    write_private(
        key_file,
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    public_file = key_dir / "gateway-public.pem"
    public_file.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    proof_key = secrets.token_bytes(32)
    started = time.time() - 1

    store = pg.get_connection(pg_url)
    store.repo_id = LOCAL_REPO_ID
    pg.init_db(store)
    registry = CatalogRegistry()
    register_work_catalog(registry, WorkApplication.postgres(store))

    common = dict(
        issuer=LOCAL_ISSUER,
        audience=LOCAL_AUDIENCE,
        environment=LOCAL_ENVIRONMENT,
        expected_workspace_id=LOCAL_WORKSPACE_ID,
        allowed_repo_ids=frozenset({LOCAL_REPO_ID}),
        key_id=LOCAL_KEY_ID,
        replay_not_before=started,
    )
    shell_resolver = GatewayAssertionIdentityResolver.from_file(
        public_file, edge_proofs=EdgeProofVerifier(proof_key, not_before=started), **common
    )
    edge_resolver = GatewayAssertionIdentityResolver.from_file(
        public_file, require_oauth_grant=True, **common
    )
    shell = create_app(
        settings=ServiceSettings(
            environment_name=LOCAL_ENVIRONMENT,
            environment_class="development",
            compatibility_state="compatible",
        ),
        registry=registry,
        identity_resolver=shell_resolver,
    )
    shell_port, edge_port = free_port(), free_port()
    shell_url = f"http://127.0.0.1:{shell_port}"
    auth = EdgeProofAuth(proof_key)
    source = ShellWorkSource(base_url=shell_url, auth=auth)
    context = ToolsetContext(
        env={}, work_source=source, runs=SprintctlRecordStore(base_url=shell_url, timeout=30, auth=auth)
    )
    edge = create_edge_app(
        identity_resolver=edge_resolver, work_source=source, toolsets=build_toolsets(context)
    )
    stack = LocalStack(key_file=key_file, shell_url=shell_url,
                       mcp_url=f"http://127.0.0.1:{edge_port}{MCP_PATH}")
    for app, port in ((shell, shell_port), (edge, edge_port)):
        server = _Server(app, port)
        server.start()
        server.wait_started()
        stack.servers.append(server)
    return stack


# ---------------------------------------------------------------------------
# The scenario.
# ---------------------------------------------------------------------------


@dataclass
class Expectations:
    transcript: Transcript
    results: list[dict[str, Any]] = field(default_factory=list)
    #: Applied to every detail before it is written or printed, so a check
    #: cannot carry items the listing scope already kept out.
    scope: Callable[[Any], Any] = lambda detail: detail

    def check(self, case: str, name: str, ok: bool, detail: Any = None) -> bool:
        detail = self.scope(detail)
        entry = {"case": case, "expectation": name, "ok": bool(ok), "detail": detail}
        self.results.append(entry)
        self.transcript.write({"kind": "expectation", **entry})
        print(f"[{'PASS' if ok else 'FAIL'}] {case}: {name}" + (f" -- {detail}" if not ok else ""))
        return bool(ok)

    @property
    def green(self) -> bool:
        return bool(self.results) and all(entry["ok"] for entry in self.results)


@dataclass
class Caller:
    name: str
    auth_kind: str
    auth: str
    headers: Callable[[], dict[str, str]]


class Scenario:
    def __init__(
        self,
        *,
        out: Path,
        mcp_url: str,
        reader: AuthorityReader,
        callers: dict[str, Caller],
        ttl: float,
        stamp: str,
    ) -> None:
        self.out = out
        self.mcp_url = mcp_url
        self.reader = reader
        self.callers = callers
        self.ttl = ttl
        self.stamp = stamp
        self.transcript = reader.transcript
        self.expect = Expectations(
            self.transcript,
            scope=lambda detail: scope_detail(detail, frozenset(self.created_items)),
        )
        self.build = _harness_build()
        self.digest = script_digest()
        self.created_items: list[int] = []
        self.sprint: dict[str, Any] | None = None
        self.ids: dict[str, int] = {}
        reader.scope = self.scope

    def scope(self, value: Any) -> Any:
        return scope_to_run_items(value, frozenset(self.created_items))

    # -- helpers ------------------------------------------------------------

    def mark(self, case: str, step: str) -> None:
        print(f"== {case}: {step}")
        self.transcript.write({"kind": "step", "case": case, "step": step})

    def state_file(self, case: str, name: str, item_id: int) -> Path:
        path = self.out / "state" / f"{case}-{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tag = f"m14-{self.stamp}-{case}-{name}"
        path.write_text(json.dumps({
            "item_id": item_id,
            "run_key": f"{tag}-run",
            "claim_key": f"{tag}-claim",
            "report_key": f"{tag}-report",
            "payload_marker": f"{tag}-payload",
            "harness_build": self.build,
            "instruction_digest": self.digest,
        }))
        return path

    def spawn(self, name: str, state: Path, action: str) -> subprocess.Popen[bytes]:
        caller = self.callers[name]
        command = [
            sys.executable, str(SCRIPT), "worker", "--name", name, "--action", action,
            "--state", str(state), "--mcp-url", self.mcp_url,
            "--transcript", str(self.transcript.path),
            "--auth-kind", caller.auth_kind, "--auth", caller.auth,
            "--heartbeat-every", str(max(1.0, self.ttl / 5)),
        ]
        return subprocess.Popen(command)

    def run_worker(self, name: str, state: Path, action: str) -> dict[str, Any]:
        process = self.spawn(name, state, action)
        code = process.wait(timeout=600)
        if code != 0:
            raise RuntimeError(f"worker {name} {action} exited {code}")
        return json.loads(state.read_text())

    def kill_after_heartbeat(self, name: str, state: Path, heartbeats: int = 1) -> dict[str, Any]:
        process = self.spawn(name, state, "claim-and-heartbeat")
        deadline = time.time() + max(60, self.ttl * 2)
        while True:
            data = json.loads(state.read_text())
            if data.get("heartbeats", 0) >= heartbeats:
                break
            if process.poll() is not None:
                raise RuntimeError(f"worker {name} exited early ({process.returncode})")
            if time.time() > deadline:
                process.kill()
                raise RuntimeError(f"worker {name} did not heartbeat")
            time.sleep(0.2)
        process.send_signal(signal.SIGKILL)
        process.wait()
        self.transcript.write({"kind": "process", "caller": name, "event": "killed",
                               "pid": process.pid, "signal": "SIGKILL",
                               "returncode": process.returncode})
        print(f"   {name} (pid {process.pid}) killed with SIGKILL after {heartbeats} heartbeat(s)")
        return json.loads(state.read_text())

    def wait_until_stale(self, item_id: int, case: str) -> None:
        self.mark(case, f"wait for the owner to see the lease on item {item_id} as stale")
        deadline = time.time() + self.ttl + 120
        while time.time() < deadline:
            lease = self.reader.invoke("work.lease.read-v1", {"item_id": item_id}, record=False)
            current = lease.get("current_lease") or {}
            if current.get("stale"):
                self.reader.invoke("work.lease.read-v1", {"item_id": item_id},
                                   note="the owner now evaluates the killed caller's lease as stale")
                return
            time.sleep(min(5.0, max(1.0, self.ttl / 10)))
        raise RuntimeError("lease never went stale")

    def public_ready(self, caller: str, note: str) -> list[int]:
        mcp = McpCaller(caller, self.mcp_url, self.callers[caller].headers, self.transcript,
                        scope=self.scope)
        mcp.initialize()
        result = mcp.call("list_ready_work", {}, note=note)
        return [item["work_id"] for item in result.get("items", [])]

    # -- setup ----------------------------------------------------------------

    def setup(self) -> dict[str, int]:
        self.mark("setup", "create a labelled disposable sprint and items")
        label = f"M1-4 scenario {self.stamp}"
        sprint = self.reader.invoke(
            "work.sprint.create",
            {"name": f"m1-4-scenario-{self.stamp}",
             "goal": "agentops#2524 settlement-scenario markers; disposable, not real work",
             "status": "active"},
        )["sprint"]
        self.sprint = sprint
        ids = self.ids
        ids["sprint"] = sprint["id"]
        for key, title in (
            ("X", f"{label} X: leased item -- disposable, not real work"),
            ("Y", f"{label} Y: depends on X -- disposable, not real work"),
            ("R", f"{label} R: restart case -- disposable, not real work"),
            ("S", f"{label} S: stale-restart case -- disposable, not real work"),
        ):
            item = self.reader.invoke(
                "work.item.create",
                {"sprint_id": sprint["id"], "track_name": "m1-4-scenario", "title": title,
                 "description": "agentops#2524 M1-4 scenario marker item; disposable, not real work."},
            )["item"]
            ids[key] = item["id"]
            self.created_items.append(item["id"])
        self.reader.invoke("work.item.dep.add", {"item_id": ids["X"], "blocked_item_id": ids["Y"]},
                           note="Y is blocked by X")
        return ids

    # -- cases ----------------------------------------------------------------

    def takeover(self, ids: dict[str, int]) -> None:
        case, x, y, sprint = "takeover", ids["X"], ids["Y"], ids["sprint"]
        check = lambda name, ok, detail=None: self.expect.check(case, name, ok, detail)  # noqa: E731

        ready = self.reader.ready_ids(sprint, note="before any claim")
        check("X is ready and Y is not before anything happens", x in ready and y not in ready, ready)

        self.mark(case, "A registers a run, claims X and heartbeats; then A is killed")
        a_state = self.state_file(case, "A", x)
        a = self.kill_after_heartbeat("A", a_state)
        check("A holds generation 1 of X's lease",
              bool(a.get("lease_id")) and a.get("generation") == 1,
              {"lease_id": a.get("lease_id"), "generation": a.get("generation")})

        self.mark(case, "B registers a run and claims X while A's lease is still fresh")
        b_state = self.state_file(case, "B", x)
        b = self.run_worker("B", b_state, "early-claim")
        check("B's claim is refused lease-held while A's lease is fresh",
              b.get("early_claim_code") == "lease-held", b.get("early_claim_code"))

        self.wait_until_stale(x, case)

        self.mark(case, "B claims X again: the owner takes A's stale lease over")
        b_caller = McpCaller("B", self.mcp_url, self.callers["B"].headers, self.transcript,
                             scope=self.scope)
        b_caller.initialize()
        b_state_data = json.loads(b_state.read_text())
        takeover_key = b_state_data["claim_key"] + "-2"
        claim = b_caller.call("claim_work", {"item_id": x, "run_id": b_state_data["run_id"],
                                             "idempotency_key": takeover_key})
        lease_b = claim["lease"]
        took_over = claim.get("took_over") or {}
        check("B's claim takes A's lease over (took_over names A's lease)",
              took_over == a["lease_id"] or (isinstance(took_over, dict)
                                             and took_over.get("lease_id") == a["lease_id"]),
              took_over)
        check("B's lease is generation 2", lease_b.get("generation") == 2, lease_b.get("generation"))
        b_caller.call("heartbeat", {"lease_id": lease_b["lease_id"], "run_id": b_state_data["run_id"]})

        self.mark(case, "A comes back (new process, same grant) and reports its late result")
        a_late = self.run_worker("A", a_state, "late-report")
        check("A's late report_outcome is refused claim-superseded",
              a_late.get("late_report_code") == "claim-superseded", a_late.get("late_report_code"))
        check("A's late heartbeat is refused claim-superseded",
              a_late.get("late_heartbeat_code") == "claim-superseded",
              a_late.get("late_heartbeat_code"))

        lease_read = self.reader.invoke("work.lease.read-v1", {"item_id": x},
                                        note="after A's late report: A's payload is on X")
        reports = lease_read.get("outcome_reports", [])
        a_reports = [r for r in reports if r.get("lease_id") == a["lease_id"]]
        check("A's refused report is retained on X with its payload",
              len(a_reports) == 1 and json.dumps(a_reports[0]).find(a["payload_marker"]) >= 0,
              a_reports)
        leases = {lease["lease_id"]: lease for lease in lease_read.get("leases", [])}
        check("the owner's lease records name the takeover both ways",
              leases.get(a["lease_id"], {}).get("state") == "superseded"
              and leases.get(a["lease_id"], {}).get("superseded_by") == lease_b["lease_id"]
              and leases.get(lease_b["lease_id"], {}).get("takeover_of") == a["lease_id"],
              list(leases.values()))
        check("the retained report is disposition rejected, reason claim-superseded",
              bool(a_reports) and a_reports[0].get("disposition") == "rejected"
              and a_reports[0].get("reason_code") == "claim-superseded", a_reports)
        ready = self.reader.ready_ids(sprint, note="after A's rejected report, before B reports")
        check("Y is not in next-work ready_items before settlement", y not in ready, ready)
        public = self.public_ready("B", "before settlement")
        check("Y is not in list_ready_work before settlement", y not in public, public)

        self.mark(case, "B reports its result under the default verification profile")
        outcome = b_caller.call("report_outcome", {
            "lease_id": lease_b["lease_id"], "run_id": b_state_data["run_id"],
            "outcome": "succeeded", "summary": f"B: scenario work on item {x} finished",
            "payload": {"caller": "B", "result": b_state_data["payload_marker"]},
            "checks": [{"name": "scenario-check", "status": "passed", "ref": f"{RECIPE_ID}#B"}],
            "idempotency_key": b_state_data["report_key"],
        })
        check("B's report settles X (settlement_effect settled)",
              outcome.get("settlement_effect") == "settled", outcome.get("settlement_effect"))

        decisions = self.reader.invoke("work.read.item-decisions", {"item_id": x},
                                       note="the authority's decision on X")
        rows = decisions.get("decisions", [])
        accepted = [d for d in rows if d.get("kind") == "accept"]
        check("exactly one accept decision on X", len(rows) == 1 and len(accepted) == 1, rows)
        check('the decision reads "accepted under verification profile checked"',
              bool(accepted) and (accepted[0].get("rationale") or "").startswith(
                  "accepted under verification profile checked:"), accepted)
        check("the decision is the authority's (sprintctl:lease-settlement)",
              bool(accepted) and accepted[0].get("actor") == "sprintctl:lease-settlement", accepted)
        final = self.reader.invoke("work.lease.read-v1", {"item_id": x},
                                   note="X's leases and reports after settlement")
        b_lease = next((lease for lease in final.get("leases", [])
                        if lease.get("lease_id") == lease_b["lease_id"]), {})
        check("X's verification bar and B's pinned bar are the named profile checked",
              (final.get("verification") or {}).get("profile") == "checked"
              and (b_lease.get("verification") or {}).get("profile") == "checked",
              {"item": final.get("verification"), "lease": b_lease.get("verification")})
        item = self.reader.invoke("work.read.item", {"item_id": x}, note="X after settlement")
        check("X is done", (item.get("item") or {}).get("status") == "done", item.get("item"))
        ready = self.reader.ready_ids(sprint, note="after settlement")
        check("Y is in next-work ready_items after settlement", y in ready, ready)
        public = self.public_ready("B", "after settlement")
        check("Y is in list_ready_work after settlement", y in public, public)

    def restart(self, ids: dict[str, int], *, stale: bool) -> None:
        case = "stale-restart" if stale else "restart"
        item_id = ids["S" if stale else "R"]
        check = lambda name, ok, detail=None: self.expect.check(case, name, ok, detail)  # noqa: E731

        self.mark(case, f"A claims item {item_id}, heartbeats, and is killed")
        state = self.state_file(case, "A", item_id)
        first = self.kill_after_heartbeat("A", state)
        if stale:
            self.wait_until_stale(item_id, case)
        self.mark(case, "A restarts: same grant, same run and claim idempotency keys")
        second = self.run_worker("A", state, "resume-and-report")
        check("the restarted A gets the same run id", second.get("resumed_run_id") == first["run_id"],
              [first["run_id"], second.get("resumed_run_id")])
        check("the restarted A resumes the same lease (resumed: true)",
              second.get("resumed_lease_id") == first["lease_id"] and second.get("resumed") is True,
              [first["lease_id"], second.get("resumed_lease_id"), second.get("resumed")])
        check("the restarted A's report settles the item",
              second.get("settlement_effect") == "settled", second.get("settlement_effect"))
        lease_read = self.reader.invoke("work.lease.read-v1", {"item_id": item_id},
                                        note=f"{case}: leases and reports")
        leases = lease_read.get("leases", [])
        reports = lease_read.get("outcome_reports", [])
        check("one lease, one report, nothing rejected",
              len(leases) == 1 and len(reports) == 1
              and all(r.get("disposition") != "rejected" for r in reports),
              {"leases": len(leases), "reports": [r.get("disposition") for r in reports]})
        decisions = self.reader.invoke("work.read.item-decisions", {"item_id": item_id},
                                       note=f"{case}: the authority's decision")
        rows = decisions.get("decisions", [])
        check("exactly one settlement (one accept decision)",
              len(rows) == 1 and rows[0].get("kind") == "accept", rows)
        if stale:
            (lease,) = leases or ({},)
            check("the stale lease was reactivated in place (same lease, generation 1, "
                  "no takeover)",
                  lease.get("lease_id") == first["lease_id"] and lease.get("generation") == 1
                  and lease.get("takeover_of") is None and lease.get("superseded_by") is None,
                  lease)

    def cleanup(self) -> None:
        self.mark("cleanup", "withdraw every disposable item the run did not settle, "
                  "then close the disposable sprint")
        for item_id in self.created_items:
            try:
                state = self.reader.invoke("work.read.item-decisions", {"item_id": item_id},
                                           record=False)
                if state.get("status") == "done":
                    continue
                self.reader.invoke(
                    "work.decision.record",
                    {"item_id": item_id, "kind": "withdraw", "evidence_digests": [],
                     "rationale": "agentops#2524 disposable scenario item; withdrawn after the run"},
                    idempotency_key=f"m14-{self.stamp}-withdraw-{item_id}",
                )
            except Exception as error:  # recorded, and the rest still runs
                self.expect.check("cleanup", f"disposable item {item_id} is withdrawn",
                                  False, repr(error))
        if self.sprint is not None:
            try:
                self.close_sprint(self.sprint)
                closed = self.reader.invoke("work.read.sprint", {"sprint_id": self.sprint["id"]},
                                            note="the disposable sprint after cleanup")
                status = (closed.get("sprint") or closed).get("status")
                self.expect.check("cleanup", "the disposable sprint is closed",
                                  status == "closed", status)
            except Exception as error:
                self.expect.check("cleanup", "the disposable sprint is closed", False, repr(error))

    def close_sprint(self, sprint: dict[str, Any]) -> None:
        """Close the run's sprint through the owner's `sprint.close` command.

        The served surface has no sprint-status operation; closing is an
        authority command (`work.lifecycle.arbitrate`), built with the
        owner's own contracts, as vuoro-cloud's restore drill retires items.
        A served owner does not pin the command's repository UUID
        (sprintctl `authority.py`), so a name-derived one is used.
        """

        import dataclasses

        from sprintctl import contracts, outbox

        actor = self.reader.invoke("work.identity.current", {}, record=False)["actor"]
        event_id = str(uuid.uuid4())
        aggregate_uuid = sprint["aggregate_uuid"]
        command = contracts.AuthorityCommand(
            event_id=event_id, record_type="sprint.close", schema_version="1",
            actor=actor, authored_at=now_iso(),
            refs={"repo_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"sprintctl-repo:{self.reader.repo_id}")),
                  "aggregate_type": "sprint", "aggregate_uuid": aggregate_uuid,
                  "aggregate_id": int(sprint["id"])},
            payload={}, basis_revision=f"sprint:{aggregate_uuid}@status:active",
            correlation_id=event_id,
        )
        with tempfile.TemporaryDirectory() as tmp:
            conn = outbox.open_outbox(Path(tmp) / "outbox.db")
            record = dataclasses.asdict(outbox.append_authority_command(conn, command))
            conn.close()
        result = self.reader.invoke(
            "work.lifecycle.arbitrate", {"record": record}, idempotency_key=event_id,
            basis_revision=record["basis_revision"],
            note="close the disposable sprint",
        )
        if result.get("outcome") not in (None, "accepted", "applied"):
            raise RuntimeError(f"sprint.close: {result}")


def _harness_build() -> str:
    try:
        sha = subprocess.run(["git", "-C", str(SCRIPT.parent), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(SCRIPT.parent), "status", "--porcelain", "--",
                                str(SCRIPT)], capture_output=True, text=True).stdout.strip()
        return f"git:{sha}{'+dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# ---------------------------------------------------------------------------
# Evidence packet.
# ---------------------------------------------------------------------------


def render_packet(entries: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    lines = [
        f"# M1-4 settlement scenario: {meta['mode']} run {meta['stamp']}",
        "",
        f"Generated by `scripts/settlement_scenario.py` ({meta['harness_build']}, "
        f"script {meta['script_digest']}).",
        f"Mode `{meta['mode']}`; lease TTL {meta['ttl']} s; MCP route `{meta['mcp_url']}`.",
        f"Result: **{'GREEN' if meta['green'] else 'RED'}** "
        f"({sum(1 for e in entries if e['kind'] == 'expectation' and e['ok'])}/"
        f"{sum(1 for e in entries if e['kind'] == 'expectation')} expectations).",
        "",
        "Every entry below is quoted from `transcript.json`, in order: tool calls on the",
        "public MCP route (caller, tool, arguments, answer), the authority's records read",
        "over `/api/invoke/v1`, process events (start, SIGKILL) and the expectation checks.",
        "",
    ]
    for entry in entries:
        kind = entry["kind"]
        if kind == "step":
            lines += [f"## {entry['case']}: {entry['step']}", ""]
        elif kind == "tool-call":
            verdict = "error" if entry["is_error"] else "ok"
            lines += [
                f"**{entry['caller']} -> `{entry['tool']}`** ({verdict}, {entry['at']})"
                + (f" -- {entry['note']}" if entry.get("note") else ""),
                "",
                "```json",
                json.dumps({"arguments": entry["arguments"], "result": entry["result"]},
                           indent=2, sort_keys=True),
                "```",
                "",
            ]
        elif kind == "authority":
            lines += [
                f"*authority* `{entry['operation']}` ({entry['status']}, {entry['at']})"
                + (f" -- {entry['note']}" if entry.get("note") else ""),
                "",
                "```json",
                json.dumps({"arguments": entry["arguments"],
                            "result": entry["result"] if entry["result"] is not None
                            else entry["error"]}, indent=2, sort_keys=True),
                "```",
                "",
            ]
        elif kind == "process":
            detail = ", ".join(f"{k}={entry[k]}" for k in ("pid", "action", "signal") if k in entry)
            lines += [f"- process: {entry['caller']} {entry['event']} ({detail}) at {entry['at']}", ""]
        elif kind == "expectation":
            mark = "PASS" if entry["ok"] else "FAIL"
            lines += [f"- **{mark}** {entry['expectation']}", ""]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------


def run_main(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    transcript = Transcript(out / "transcript.jsonl")
    if transcript.path.exists():
        transcript.path.unlink()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work = out / "state"
    work.mkdir(exist_ok=True)

    with contextlib.ExitStack() as stack_ctx:
        if args.mode == "local":
            pg_url = stack_ctx.enter_context(postgres(args.pg_url))
            # The local gateway key lives outside --out, so no upload or
            # commit of the evidence directory can carry it, even on a crash.
            key_dir = Path(stack_ctx.enter_context(
                tempfile.TemporaryDirectory(prefix="m14-gateway-")))
            stack = start_local_stack(pg_url, key_dir, args.ttl)
            stack_ctx.callback(stack.stop)
            ttl = float(args.ttl)
            mcp_url = stack.mcp_url
            specs = {
                "A": LocalGatewaySpec(str(stack.key_file), "github:1001", "grant-A-" + stamp,
                                      tuple(CALLER_AUTHORITIES)),
                "B": LocalGatewaySpec(str(stack.key_file), "github:1002", "grant-B-" + stamp,
                                      tuple(CALLER_AUTHORITIES)),
            }
            callers = {
                name: Caller(name, "local", spec.to_arg(), local_headers(spec))
                for name, spec in specs.items()
            }
            reader_spec = LocalGatewaySpec(str(stack.key_file), "github:1000", None,
                                           tuple(READER_AUTHORITIES))
            reader = AuthorityReader(stack.shell_url, LOCAL_REPO_ID, local_headers(reader_spec),
                                     transcript, send_catalog_revision=True)
        else:
            ttl = float(args.ttl)
            mcp_url = args.mcp_url
            secret = Path(args.client_secret_file).expanduser()
            callers = {}
            for name, token in (("A", args.token_a), ("B", args.token_b)):
                token_path = Path(token).expanduser()
                auth = f"{token_path}{os.pathsep}{secret}"
                callers[name] = Caller(name, "oauth", auth, oauth_headers(token_path, secret))
            reader = AuthorityReader(args.api_url, args.repo_id,
                                     bearer_file_headers(Path(args.pat_file).expanduser()),
                                     transcript, send_catalog_revision=False)

        scenario = Scenario(out=out, mcp_url=mcp_url, reader=reader, callers=callers,
                            ttl=ttl, stamp=stamp)
        transcript.write({"kind": "meta", "mode": args.mode, "ttl": ttl, "mcp_url": mcp_url,
                          "harness_build": scenario.build, "script_digest": scenario.digest})
        for name in ("A", "B"):
            probe = McpCaller(name, mcp_url, callers[name].headers, transcript)
            probe.initialize()
            tools = probe.tools()
            scenario.expect.check("setup", f"{name} is offered claim_work, heartbeat and "
                                  "report_outcome", {"claim_work", "heartbeat", "report_outcome"}
                                  <= set(tools), tools)
        ids = scenario.ids
        cases = args.cases.split(",")
        try:
            scenario.setup()
            if "takeover" in cases:
                scenario.takeover(ids)
            if "restart" in cases:
                scenario.restart(ids, stale=False)
            if "stale-restart" in cases:
                scenario.restart(ids, stale=True)
        except Exception as error:  # a crash is a red run, with the transcript kept
            scenario.expect.check("run", "the scenario ran to completion", False, repr(error))
        finally:
            scenario.cleanup()

    entries = transcript.read()
    meta = {"mode": args.mode, "stamp": stamp, "ttl": ttl, "mcp_url": mcp_url,
            "harness_build": scenario.build, "script_digest": scenario.digest,
            "green": scenario.expect.green, "item_ids": ids}
    (out / "transcript.json").write_text(json.dumps({"meta": meta, "entries": entries},
                                                    indent=2, sort_keys=True) + "\n")
    transcript.path.unlink()
    (out / "transcript.md").write_text(render_packet(entries, meta))
    for path in sorted(work.glob("*")):
        path.unlink()
    work.rmdir()
    print(f"{'GREEN' if scenario.expect.green else 'RED'}: evidence in {out}")
    return 0 if scenario.expect.green else 1


def oauth_login_main(args: argparse.Namespace) -> int:
    """Authorization code + PKCE for one claude-connector grant (live mode).

    Prints the authorize URL for the operator's browser, receives the code on
    the loopback redirect, and writes access/refresh tokens to --token (0600).
    Each run is a new consent and so a new grant (a distinct lease binding).
    """

    from http.server import BaseHTTPRequestHandler, HTTPServer

    meta = httpx.get(f"{args.api_url.rstrip('/')}/.well-known/oauth-authorization-server",
                     timeout=30).json()
    resource = f"{args.api_url.rstrip('/')}/mcp"
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    redirect = f"http://127.0.0.1:{args.port}/callback"
    url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": redirect,
        "scope": "vuoro:work.read vuoro:evidence.record vuoro:work.claim",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": resource,
    })
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            received.update(query)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"M1-4 scenario: grant received; you can close this tab.")

        def log_message(self, *_: Any) -> None:
            pass

    print(f"Open in the browser signed in to vuoro.cloud, choose workspace kotona, Allow:\n{url}")
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    while "code" not in received and "error" not in received:
        server.handle_request()
    if received.get("state") != state or "code" not in received:
        raise SystemExit(f"authorization failed: {received.get('error', 'state mismatch')}")
    secret = Path(args.client_secret_file).expanduser().read_text().strip()
    response = httpx.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": received["code"],
        "redirect_uri": redirect, "code_verifier": verifier, "resource": resource,
    }, auth=(CLIENT_ID, secret), timeout=30)
    response.raise_for_status()
    body = response.json()
    token = Path(args.token).expanduser()
    token.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir or not token.resolve().is_relative_to(Path(runtime_dir).resolve()):
        print("warning: the token file is not under $XDG_RUNTIME_DIR (tmpfs); refresh "
              "rewrites it, and a disk filesystem may keep earlier versions")
    claims = json.loads(base64.urlsafe_b64decode(
        body["access_token"].split(".")[1] + "=" * (-len(body["access_token"].split(".")[1]) % 4)))
    write_private(token, json.dumps({
        "access_token": body["access_token"], "refresh_token": body["refresh_token"],
        "expires_at": time.time() + int(body.get("expires_in", 900)),
        "token_endpoint": meta["token_endpoint"], "resource": resource,
        "scope": body.get("scope"), "grant_id": claims.get("grant_id"),
        "workspace_id": claims.get("workspace_id"),
    }))
    print(f"wrote {token} (scope: {body.get('scope')}; grant_id {claims.get('grant_id')}; "
          f"workspace_id {claims.get('workspace_id')}) -- revoke this grant after the run")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the scenario and write the evidence packet")
    run.add_argument("--mode", choices=("local", "live"), default="local")
    run.add_argument("--out", default="_artifacts/m1-4-settlement-scenario")
    run.add_argument("--cases", default="takeover,restart,stale-restart")
    run.add_argument("--ttl", type=int, default=30,
                     help="local: the owner's lease TTL; live: the tenant runtime's TTL")
    run.add_argument("--pg-url", help="local: an existing disposable database")
    run.add_argument("--api-url", default="https://api.vuoro.cloud")
    run.add_argument("--mcp-url", default="https://api.vuoro.cloud/mcp")
    run.add_argument("--repo-id", default="vuoro")
    run.add_argument("--token-a", help="live: caller A's grant (written by oauth-login)")
    run.add_argument("--token-b", help="live: caller B's grant (written by oauth-login)")
    run.add_argument("--pat-file", help="live: workspace PAT with work:sprint/lifecycle")
    run.add_argument("--client-secret-file",
                     default="~/.config/vuoro/credentials/claude-connector.secret")
    run.set_defaults(func=run_main)

    login = sub.add_parser("oauth-login", help="live: mint one claude-connector grant")
    login.add_argument("--token", required=True)
    login.add_argument("--api-url", default="https://api.vuoro.cloud")
    login.add_argument("--port", type=int, default=53682)
    login.add_argument("--client-secret-file",
                       default="~/.config/vuoro/credentials/claude-connector.secret")
    login.set_defaults(func=oauth_login_main)

    worker = sub.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--name", required=True)
    worker.add_argument("--action", required=True)
    worker.add_argument("--state", required=True)
    worker.add_argument("--mcp-url", required=True)
    worker.add_argument("--transcript", required=True)
    worker.add_argument("--auth-kind", choices=("local", "oauth"), required=True)
    worker.add_argument("--auth", required=True)
    worker.add_argument("--heartbeat-every", type=float, default=6.0)
    worker.set_defaults(func=worker_main)

    args = parser.parse_args(argv)
    if args.command == "run" and args.mode == "live":
        missing = [flag for flag in ("token_a", "token_b", "pat_file") if not getattr(args, flag)]
        if missing:
            parser.error("live mode needs " + ", ".join("--" + m.replace("_", "-") for m in missing))
        # Cleanup closes the disposable sprint with the owner's own contracts
        # (`close_sprint`). Without the pinned wheel that step fails only at
        # the very end, after the grants and PAT have been spent.
        if importlib.util.find_spec("sprintctl") is None or importlib.util.find_spec("sprintctl.contracts") is None:
            parser.error("live mode needs the pinned sprintctl wheel to close its sprint;"
                         " install it as in the packet README's 'Re-running' section")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
