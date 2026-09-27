"""A strict MCP 2026-07-28 client for the `mcp-strict-client` CI job (agentops#2522).

Why: the ``resultType``/``cacheScope`` regression reached production because
claude.ai is a lenient client and Claude Code a strict one.  This client is
strict on purpose.  It talks to ``vuoro-service mcp-serve`` running from the
service image exactly as a 2026-07-28 client does (no handshake;
``MCP-Protocol-Version``, ``Mcp-Method`` and ``Mcp-Name`` headers) and
checks every response it gets:

* against the published MCP 2026-07-28 JSON schema, vendored and pinned in
  ``scripts/mcp_schema/`` (source commit and SHA-256 below; the file is
  refused if its digest differs);
* against the value rules strict clients enforce beyond that schema, which
  types ``resultType`` only as a string: ``resultType`` is a completion kind
  (``complete`` | ``input_required`` | ``task``, and this server has only
  ``complete`` flows), and ``cacheScope``/``ttlMs`` are well-formed wherever
  they appear (the schema enumerates ``cacheScope`` only on cacheable
  results).

It then runs the D-044 isolation test against two edges (workspaces A and B,
one gateway signing key, as the gateway mints them): each workspace's
assertion reaches only its own runtime, the other workspace's edge refuses it
before any upstream call, and nothing one caller sends reaches the other's
upstream.  vuoro-cloud's D-044 tests run at the gateway
(``tests/test_gateway_mcp_integration.py``) and live on the cluster
(``docs/evidence/2026-09-25-d044-live-isolation.md``); this is the equivalent
at the containerised edge, reimplemented here rather than checked out.

Any deviation fails the run, except those listed in `KNOWN_DEVIATIONS`
(empty since agentops#2526), which would be server behaviours that differ
from the published schema and are tracked for a fix.  Those are strict in
the other direction too: a known deviation that is no longer observed fails
the run until it is removed from the list, so the list cannot go stale.

Subcommands::

    setup DIR     ephemeral Ed25519 test signer, mounts and env files
    check ...     run every check against running containers

The signer is generated per run and never leaves ``DIR``; it is a test
assertion signer, not a secret of any deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# ---------------------------------------------------------------------------
# The pinned schema
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2026-07-28"
SCHEMA_PATH = Path(__file__).resolve().parent / "mcp_schema" / f"{PROTOCOL_VERSION}.schema.json"
SCHEMA_SOURCE = (
    "https://github.com/modelcontextprotocol/modelcontextprotocol/blob/"
    "271ecc9accafdd9b83a3c869fa67c22953b2af80/schema/2026-07-28/schema.json"
)
SCHEMA_SHA256 = "ef70b61f99b6d2e5e3b46863822eab08dff6a45bedc7a08914e0e5b133f40203"

#: Completion kinds a 2026-07-28 client accepts in ``resultType``.  A
#: per-method value ("tools-list-result") made Claude Code 2.1.283 drop every
#: vuoro tool ("Unsupported result type") on 2026-09-26.
RESULT_TYPES = frozenset({"complete", "input_required", "task"})
CACHE_SCOPES = frozenset({"public", "private"})

ID_NULL = '"id": null in an error response'


@dataclass(frozen=True)
class KnownDeviation:
    """A tracked server deviation: why, and the exact problem messages it
    produces.  Only those messages are excused, and only under this check
    id; any other problem from the same check still fails the run."""

    note: str
    messages: frozenset[str]


#: Server behaviours that differ from the pinned schema today, by check id.
#: Each is observed on every run; one that stops being observed fails the
#: run so this list is edited in the same change that fixes it.  Empty since
#: agentops#2526 fixed the four the job landed with (ping without
#: resultType, "id": null on errors, -32020 over HTTP 200, and -32600 for an
#: unsupported version): a new deviation is fixed in
#: vuoro_mcp_edge/server.py, not listed here.
KNOWN_DEVIATIONS: dict[str, KnownDeviation] = {}

# ---------------------------------------------------------------------------
# Edge proof / replay protection (vuoro#134, agentops#2519)
# ---------------------------------------------------------------------------
#: #134 is merged: the edge consumes each assertion's jti (a second
#: presentation is HTTP 401, -32003) and signs every upstream call with an
#: X-Vuoro-Edge-Proof, and both are checked.  The container wiring #134
#: requires (VUORO_EDGE_PROOF_KEY_FILE on a writable tmpfs) is in
#: scripts/mcp_strict_client.sh, and every assertion minted below carries a
#: fresh jti plus client_id and grant_id.
ENFORCE_EDGE_PROOF_AND_REPLAY = True
JSONRPC_ASSERTION_REPLAYED = -32003

# ---------------------------------------------------------------------------
# The test identities (setup and check agree on these)
# ---------------------------------------------------------------------------

ISSUER = "vuoro-ci-strict-client"
AUDIENCE = "vuoro-service"
KEY_ID = "ci-strict-2026-09"
AUTHORITIES = ["work:read"]
CLIENT_ID = "claude-connector"

_AS_OF = "2026-09-27T12:00:00Z"


def _item(work_id: int, title: str, *, status: str = "pending", blocked_by: list[int] | None = None) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "title": title,
        "priority": 1,
        "status": status,
        "blocked": bool(blocked_by),
        "updated_at": _AS_OF,
        "created_at": _AS_OF,
        "resolution": "done" if status == "done" else None,
        "blocked_by": blocked_by or [],
    }


@dataclass(frozen=True)
class Workspace:
    key: str
    workspace_id: str
    environment: str
    repo_id: str
    items: tuple[dict[str, Any], ...]

    @property
    def ready_ids(self) -> list[int]:
        return [i["work_id"] for i in self.items if i["status"] == "pending" and not i["blocked"]]

    def title(self, work_id: int) -> str:
        return next(i["title"] for i in self.items if i["work_id"] == work_id)


#: Both workspaces have a work item 1 with different titles, so reading the
#: wrong runtime cannot pass unnoticed; item 3 exists only in A.
WORKSPACES: dict[str, Workspace] = {
    "a": Workspace(
        key="a",
        workspace_id="01KCSTR1CTA0000000000000AA",
        environment="vuoro-cloud-ws-01kcstr1cta0",
        repo_id="repo-a",
        items=(
            _item(1, "A one: ready"),
            _item(2, "A two: blocked by one", blocked_by=[1]),
            _item(3, "A three: done", status="done"),
        ),
    ),
    "b": Workspace(
        key="b",
        workspace_id="01KCSTR1CTB0000000000000BB",
        environment="vuoro-cloud-ws-01kcstr1ctb0",
        repo_id="repo-b",
        items=(_item(1, "B one: ready"), _item(7, "B seven: ready")),
    ),
}

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    millis = int(time.time() * 1000)
    head = "".join(_CROCKFORD[(millis >> (5 * i)) & 31] for i in reversed(range(10)))
    return head + "".join(secrets.choice(_CROCKFORD) for _ in range(16))


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def setup(state: Path) -> None:
    """Write the ephemeral signer, each edge's /etc/vuoro mount and env files."""

    state.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    signer = state / "signer"
    signer.mkdir(mode=0o700, exist_ok=True)
    key_file = signer / "private.pem"
    descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    for ws in WORKSPACES.values():
        root = state / f"etc-vuoro-{ws.key}"
        (root / "identity").mkdir(parents=True, exist_ok=True)
        (root / "bindings").mkdir(exist_ok=True)
        (root / "identity" / "gateway-public.pem").write_bytes(public_pem)
        (root / "bindings" / "bindings.json").write_text(
            json.dumps(
                {
                    "schema_version": "vuoro-project-bindings/v1",
                    "environment": ws.environment,
                    "projects": [
                        {
                            "project_id": new_ulid(),
                            "descriptor_digest": "sha256:" + "c" * 64,
                            "repositories": [
                                {"repo_id": ws.repo_id, "git_remote": None, "commit_sha": None}
                            ],
                        }
                    ],
                }
            )
        )
        # Read by the image's non-root user (65532).
        for path in (root, root / "identity", root / "bindings"):
            path.chmod(0o755)
        for path in (root / "identity" / "gateway-public.pem", root / "bindings" / "bindings.json"):
            path.chmod(0o644)
        _write_env(
            state / f"edge-{ws.key}.env",
            {
                "VUORO_ENVIRONMENT_NAME": ws.environment,
                "VUORO_WORKSPACE_ID": ws.workspace_id,
                "VUORO_GATEWAY_PUBLIC_KEY_FILE": "/etc/vuoro/identity/gateway-public.pem",
                "VUORO_PROJECT_BINDINGS_FILE": "/etc/vuoro/bindings/bindings.json",
                "VUORO_GATEWAY_ASSERTION_ISSUER": ISSUER,
                "VUORO_GATEWAY_ASSERTION_KEY_ID": KEY_ID,
                "VUORO_MCP_UPSTREAM_URL": f"http://stub-{ws.key}:8080",
            },
        )
        _write_env(
            state / f"stub-{ws.key}.env",
            {
                "STUB_WORKSPACE_ID": ws.workspace_id,
                "STUB_REPO_ID": ws.repo_id,
                "STUB_ITEMS": json.dumps(list(ws.items)),
                "STUB_GATEWAY_PUBLIC_KEY_FILE": "/etc/vuoro/identity/gateway-public.pem",
                "STUB_ISSUER": ISSUER,
                "STUB_AUDIENCE": AUDIENCE,
                "STUB_KEY_ID": KEY_ID,
            },
        )


def _write_env(path: Path, values: Mapping[str, str]) -> None:
    # docker --env-file: one NAME=value per line, value taken literally.
    path.write_text("".join(f"{name}={value}\n" for name, value in values.items()))


# ---------------------------------------------------------------------------
# Schema validation and value rules (pure; unit-tested)
# ---------------------------------------------------------------------------


class Schema:
    """The pinned 2026-07-28 schema, one validator per definition."""

    def __init__(self, path: Path = SCHEMA_PATH, sha256: str = SCHEMA_SHA256) -> None:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != sha256:
            raise SystemExit(
                f"{path} has SHA-256 {digest}, not the pinned {sha256}; "
                f"re-vendor it from {SCHEMA_SOURCE}"
            )
        self._document = json.loads(raw)
        self._validators: dict[str, jsonschema.protocols.Validator] = {}

    def errors(self, definition: str, instance: Any) -> list[str]:
        validator = self._validators.get(definition)
        if validator is None:
            if definition not in self._document["$defs"]:
                raise KeyError(f"the pinned schema has no definition {definition}")
            schema = {"$ref": f"#/$defs/{definition}", "$defs": self._document["$defs"]}
            validator = jsonschema.Draft202012Validator(schema)
            self._validators[definition] = validator
        return [
            f"{definition}: {'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
            for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
        ]


def result_value_errors(result: Any, *, expect_result_type: str = "complete") -> list[str]:
    """What a strict 2026-07-28 client refuses beyond the schema's types."""

    if not isinstance(result, dict):
        return [f"result is {type(result).__name__}, not an object"]
    problems: list[str] = []
    result_type = result.get("resultType")
    if result_type not in RESULT_TYPES:
        problems.append(
            f"resultType {result_type!r} is not a completion kind {sorted(RESULT_TYPES)} "
            "(a strict client drops the result: 'Unsupported result type')"
        )
    elif result_type != expect_result_type:
        problems.append(f"resultType is {result_type!r}, expected {expect_result_type!r}")
    if "cacheScope" in result and result["cacheScope"] not in CACHE_SCOPES:
        problems.append(f"cacheScope {result['cacheScope']!r} is not one of {sorted(CACHE_SCOPES)}")
    if "ttlMs" in result:
        ttl = result["ttlMs"]
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0:
            problems.append(f"ttlMs {ttl!r} is not a non-negative integer")
    return problems


def tool_error_errors(result: Any, code: str) -> list[str]:
    """A vuoro tool error: isError true, structuredContent.error {code, message}."""

    if not isinstance(result, dict):
        return ["result is not an object"]
    problems: list[str] = []
    if result.get("isError") is not True:
        problems.append(f"isError is {result.get('isError')!r}, expected true")
    error = (result.get("structuredContent") or {}).get("error")
    if not isinstance(error, dict):
        return [*problems, "structuredContent.error is missing"]
    if error.get("code") != code:
        problems.append(f"structuredContent.error.code is {error.get('code')!r}, expected {code!r}")
    if not isinstance(error.get("message"), str) or not error["message"]:
        problems.append("structuredContent.error.message is not a non-empty string")
    content = result.get("content") or []
    if not any(block.get("type") == "text" and code in block.get("text", "") for block in content if isinstance(block, dict)):
        problems.append("no text content block names the error code")
    return problems


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    observed_known: dict[str, list[str]] = field(default_factory=dict)

    def record(self, check_id: str, label: str, problems: list[str]) -> bool:
        if not problems:
            self.passed.append(f"{check_id}: {label}")
            print(f"PASS  {check_id}: {label}")
            return True
        known = KNOWN_DEVIATIONS.get(check_id)
        excused = [p for p in problems if known is not None and p in known.messages]
        unexpected = [p for p in problems if p not in excused]
        if excused:
            self.observed_known.setdefault(check_id, []).extend(excused)
            print(f"KNOWN {check_id}: {label}")
            for problem in excused:
                print(f"        {problem}")
        if unexpected:
            for problem in unexpected:
                self.failures.append((check_id, f"{label}: {problem}"))
            print(f"FAIL  {check_id}: {label}")
            for problem in unexpected:
                print(f"        {problem}")
        return False

    def finish(self) -> int:
        stale = sorted(set(KNOWN_DEVIATIONS) - set(self.observed_known))
        for check_id in stale:
            self.failures.append(
                (
                    check_id,
                    "listed in KNOWN_DEVIATIONS but no longer observed: the server "
                    "now conforms here, so remove it from the list",
                )
            )
        print()
        print(
            f"{len(self.passed)} passed, {len(self.failures)} failed, "
            f"{len(self.observed_known)} known deviation(s) observed"
        )
        if self.failures:
            print("\nDEVIATIONS (the job fails on each):")
            for check_id, message in self.failures:
                print(f"  - [{check_id}] {message}")
            return 1
        return 0


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class Signer:
    def __init__(self, private_key_file: Path) -> None:
        key = serialization.load_pem_private_key(private_key_file.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SystemExit("the test signer must be Ed25519")
        self._key = key

    def mint(
        self,
        ws: Workspace,
        *,
        workspace_id: str | None = None,
        repo_ids: list[str] | None = None,
        authorities: list[str] | None = None,
    ) -> tuple[str, str]:
        """A fresh gateway assertion and its request id (one per request).

        [edge-proof/replay, vuoro#134] Each request gets a new jti, and the
        assertion carries client_id and grant_id as the gateway's OAuth path
        mints them: #134's edge consumes each jti once and, while the edge
        and shell share an audience, refuses assertions without both.
        """

        request_id = new_ulid()
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": f"github:{ws.key}-ci",
            "actor": f"github:{ws.key}-ci",
            "subject": new_ulid(),
            "principal_epoch": 0,
            "workspace_id": workspace_id or ws.workspace_id,
            "authorities": authorities or AUTHORITIES,
            "repo_ids": repo_ids or [ws.repo_id],
            "request_id": request_id,
            "jti": request_id,
            "client_id": CLIENT_ID,
            "grant_id": new_ulid(),
            "iat": now,
            "nbf": now - 1,
            "exp": now + 30,
        }
        token = jwt.encode(claims, self._key, algorithm="EdDSA", headers={"kid": KEY_ID, "typ": "JWT"})
        return token, request_id


class StrictClient:
    def __init__(self, url: str, signer: Signer, ws: Workspace) -> None:
        self.url = url.rstrip("/") + "/mcp"
        self.signer = signer
        self.ws = ws
        self._next_id = 0
        self._http = httpx.Client(timeout=15)

    def rpc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def headers(
        self,
        method: str | None,
        name: str | None = None,
        *,
        token: tuple[str, str] | None = None,
        authenticated: bool = True,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if method is not None:
            headers["Mcp-Method"] = method
        if name is not None:
            headers["Mcp-Name"] = name
        if authenticated:
            assertion, request_id = token or self.signer.mint(self.ws)
            headers["X-Vuoro-Identity"] = assertion
            headers["X-Request-Id"] = request_id
        headers.update(extra or {})
        return headers

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        **header_options: Any,
    ) -> tuple[int, httpx.Response]:
        rpc_id = self.rpc_id()
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
        if params is not None:
            body["params"] = params
        name = params.get("name") if method == "tools/call" and params else None
        response = self._http.post(self.url, json=body, headers=self.headers(method, name, **header_options))
        return rpc_id, response

    def raw(self, content: bytes, headers: Mapping[str, str]) -> httpx.Response:
        return self._http.post(self.url, content=content, headers=dict(headers))

    def call(self, tool: str, arguments: dict[str, Any] | None = None, **header_options: Any) -> tuple[int, httpx.Response]:
        params: dict[str, Any] = {"name": tool}
        if arguments is not None:
            params["arguments"] = arguments
        return self.request("tools/call", params, **header_options)


def _json_body(response: httpx.Response) -> tuple[Any, list[str]]:
    content_type = response.headers.get("content-type", "")
    problems = []
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        problems.append(f"Content-Type is {content_type!r}, expected application/json")
    try:
        return response.json(), problems
    except ValueError:
        return None, [*problems, f"body is not JSON: {response.text[:200]!r}"]


class Checks:
    def __init__(self, schema: Schema, report: Report) -> None:
        self.schema = schema
        self.report = report

    # -- building blocks ----------------------------------------------------

    def result(
        self,
        check_id: str,
        label: str,
        exchange: tuple[int, httpx.Response],
        definition: str,
        *,
        extra: Callable[[dict[str, Any]], list[str]] | None = None,
    ) -> dict[str, Any] | None:
        rpc_id, response = exchange
        body, problems = _json_body(response)
        if response.status_code != 200:
            problems.append(f"HTTP {response.status_code}, expected 200")
        if isinstance(body, dict):
            problems += self.schema.errors(definition, body)
            if body.get("id") != rpc_id:
                problems.append(f"id is {body.get('id')!r}, expected {rpc_id!r}")
            if "error" in body:
                problems.append(f"an error response where a result was expected: {body['error']!r}")
            result = body.get("result")
            problems += result_value_errors(result)
            if extra is not None and isinstance(result, dict) and not problems:
                problems += extra(result)
        elif body is not None:
            problems.append("body is not a JSON-RPC object")
        self.report.record(check_id, label, problems)
        return body.get("result") if isinstance(body, dict) else None

    def error(
        self,
        check_id: str,
        label: str,
        response: httpx.Response,
        *,
        http_status: int,
        code: int,
        definition: str | None,
        rpc_id: Any = None,
        pre_parse: bool = False,
        status_check_id: str | None = None,
        wrapper: str = "JSONRPCErrorResponse",
    ) -> dict[str, Any] | None:
        """An MCP error response.  `definition` is the pinned schema's
        definition for the error object (e.g. MethodNotFoundError), or None
        for an implementation-defined code; `wrapper` is the response
        definition when the schema defines the whole response.

        `rpc_id` is the id the request carried (None when it has none, e.g.
        invalid JSON or a batch).  `pre_parse` marks a refusal the server
        makes before reading the body (401, unsupported version), where it
        may omit the id.  A null id is always a failure (2026-07-28 types id
        string | integer and makes it optional), a present id must equal
        `rpc_id`, and where the server read the request it must echo it."""

        body, problems = _json_body(response)
        status_problems = (
            [] if response.status_code == http_status else [f"HTTP {response.status_code}, expected {http_status}"]
        )
        if status_check_id is None:
            problems += status_problems
        else:
            self.report.record(status_check_id, f"{label} HTTP status", status_problems)
        if isinstance(body, dict):
            if "id" in body:
                if body["id"] is None:
                    problems.append(ID_NULL)
                elif rpc_id is None:
                    problems.append(f"id is {body['id']!r} for a request without one")
                elif body["id"] != rpc_id:
                    problems.append(f"id is {body['id']!r}, expected {rpc_id!r}")
            elif rpc_id is not None and not pre_parse:
                problems.append(f"id is missing, expected {rpc_id!r} (the server read the request)")
            problems += self.schema.errors(wrapper, body)
            if "result" in body:
                problems.append("an error response carries a result")
            error = body.get("error")
            if isinstance(error, dict):
                if definition is not None:
                    problems += self.schema.errors(definition, error)
                if error.get("code") != code:
                    problems.append(f"error.code is {error.get('code')!r}, expected {code}")
        elif body is not None:
            problems.append("body is not a JSON-RPC object")
        self.report.record(check_id, label, problems)
        return body if isinstance(body, dict) else None

    # -- conformance ----------------------------------------------------------

    def conformance(self, client: StrictClient) -> None:
        ws = client.ws
        discover = self.result(
            "discover", "server/discover", client.request("server/discover"), "DiscoverResultResponse",
            extra=lambda r: [] if PROTOCOL_VERSION in r.get("supportedVersions", []) else [
                f"supportedVersions {r.get('supportedVersions')!r} lacks {PROTOCOL_VERSION}"
            ],
        )

        def tools_list_rules(result: dict[str, Any]) -> list[str]:
            names = [tool["name"] for tool in result["tools"]]
            problems = []
            if len(names) != len(set(names)):
                problems.append(f"duplicate tool names in {names}")
            for required in ("list_ready_work", "describe_work"):
                if required not in names:
                    problems.append(f"{required} is not listed")
            for tool in result["tools"]:
                if tool["inputSchema"].get("type") != "object":
                    problems.append(f"{tool['name']}.inputSchema.type is not 'object'")
            if isinstance(discover, dict) and "tools" in discover:
                discovered = [tool.get("name") for tool in discover["tools"]]
                if discovered != names:
                    problems.append(f"server/discover lists {discovered}, tools/list {names}")
            return problems

        self.result("tools-list", "tools/list", client.request("tools/list"), "ListToolsResultResponse", extra=tools_list_rules)

        def structured_matches_text(result: dict[str, Any]) -> list[str]:
            texts = [b.get("text") for b in result.get("content", []) if b.get("type") == "text"]
            try:
                parsed = [json.loads(t) for t in texts]
            except ValueError:
                return ["the text content block is not the structured result as JSON"]
            return [] if result.get("structuredContent") in parsed else [
                "no text content block carries the structuredContent"
            ]

        def ready_list(result: dict[str, Any]) -> list[str]:
            problems = [] if result.get("isError") is False else [f"isError is {result.get('isError')!r}"]
            items = (result.get("structuredContent") or {}).get("items")
            got = [item.get("work_id") for item in items or []]
            if got != ws.ready_ids:
                problems.append(f"ready work_ids {got}, expected {ws.ready_ids}")
            return problems + structured_matches_text(result)

        self.result(
            "tools-call.success", "tools/call list_ready_work", client.call("list_ready_work"),
            "CallToolResultResponse", extra=ready_list,
        )

        def describe_two(result: dict[str, Any]) -> list[str]:
            item = (result.get("structuredContent") or {}).get("item") or {}
            problems = [] if result.get("isError") is False else [f"isError is {result.get('isError')!r}"]
            if item.get("work_id") != 2 or item.get("blocked_by") != [1]:
                problems.append(f"describe_work(2) returned {item!r}")
            return problems

        self.result(
            "tools-call.success", "tools/call describe_work(2)", client.call("describe_work", {"work_id": 2}),
            "CallToolResultResponse", extra=describe_two,
        )
        self.result(
            "tools-call.tool-error", "tools/call describe_work(999): upstream not-found",
            client.call("describe_work", {"work_id": 999}), "CallToolResultResponse",
            extra=lambda r: tool_error_errors(r, "item-not-found"),
        )
        self.result(
            "tools-call.tool-error", "tools/call describe_work with a string work_id",
            client.call("describe_work", {"work_id": "two"}), "CallToolResultResponse",
            extra=lambda r: tool_error_errors(r, "invalid-params"),
        )
        self.result(
            "tools-call.tool-error", "tools/call of an unknown tool",
            client.call("no_such_tool", {}), "CallToolResultResponse",
            extra=lambda r: tool_error_errors(r, "unknown-tool"),
        )

        # -- protocol-level errors ----------------------------------------
        rpc_id, response = client.request("vuoro/no-such-method")
        self.error("error.method-not-found", "unknown method", response, http_status=200,
                   code=-32601, definition="MethodNotFoundError", rpc_id=rpc_id)

        self.error(
            "error.parse", "invalid JSON body",
            client.raw(b'{"jsonrpc": "2.0", "id": 1, "method":', client.headers(None)),
            http_status=200, code=-32700, definition="ParseError",
        )
        self.error(
            "error.invalid-request", "a JSON-RPC batch",
            client.raw(json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]).encode(),
                       client.headers(None)),
            http_status=200, code=-32600, definition="InvalidRequestError",
        )
        body = {"jsonrpc": "2.0", "id": 41, "method": "server/discover"}
        self.error(
            "header-mismatch.error", "Mcp-Method header disagreeing with the body",
            client.raw(json.dumps(body).encode(), client.headers("tools/list")),
            http_status=400, code=-32020, definition=None, rpc_id=41,
            status_check_id="header-mismatch.http-status", wrapper="HeaderMismatchError",
        )
        headers = client.headers("tools/list", extra={"MCP-Protocol-Version": "2099-01-01"})
        response = client.raw(json.dumps({"jsonrpc": "2.0", "id": 42, "method": "tools/list"}).encode(), headers)
        seen = self.error(
            "unsupported-version.error", "unsupported MCP-Protocol-Version",
            response, http_status=400, code=-32022, definition=None, rpc_id=42, pre_parse=True,
            status_check_id="unsupported-version.http-status",
            wrapper="UnsupportedProtocolVersionError",
        )
        data = ((seen or {}).get("error") or {}).get("data") or {}
        self.report.record(
            "unsupported-version.data", "unsupported MCP-Protocol-Version names the versions",
            [] if data.get("requested") == "2099-01-01" and PROTOCOL_VERSION in data.get("supported", [])
            else [f"error.data is {data!r}, expected requested 2099-01-01 and {PROTOCOL_VERSION} supported"],
        )
        rpc_id, response = client.request("tools/list", authenticated=False)
        self.error("error.unauthenticated", "no gateway assertion", response,
                   http_status=401, code=-32001, definition="Error", rpc_id=rpc_id, pre_parse=True)
        if ENFORCE_EDGE_PROOF_AND_REPLAY:
            token = client.signer.mint(ws)
            self.result("replay.first-use", "an assertion's first presentation",
                        client.request("tools/list", token=token), "ListToolsResultResponse")
            rpc_id, response = client.request("tools/list", token=token)
            self.error("replay.refused", "the same assertion presented twice", response,
                       http_status=401, code=JSONRPC_ASSERTION_REPLAYED, definition="Error",
                       rpc_id=rpc_id, pre_parse=True)
        rpc_id, response = client.request("ping")
        self.ping(rpc_id, response)

    def ping(self, rpc_id: int, response: httpx.Response) -> None:
        """ping: EmptyResult (Result) requires resultType.  Transport and id
        are recorded apart from the envelope, so each fails under its name."""

        body, problems = _json_body(response)
        if response.status_code != 200:
            problems.append(f"HTTP {response.status_code}, expected 200")
        envelope: list[str] = []
        if isinstance(body, dict):
            if body.get("id") != rpc_id:
                problems.append(f"id is {body.get('id')!r}, expected {rpc_id!r}")
            if "error" in body:
                problems.append(f"an error response to ping: {body['error']!r}")
            envelope = self.schema.errors("JSONRPCResultResponse", body)
            envelope += result_value_errors(body.get("result"))
        else:
            problems.append("body is not a JSON-RPC object")
        self.report.record("ping.transport", "ping answered 200 with its id", problems)
        self.report.record("ping.envelope", "ping", envelope)

    # -- D-044 isolation ------------------------------------------------------

    def isolation(self, clients: dict[str, StrictClient], stubs: dict[str, str]) -> None:
        http = httpx.Client(timeout=10)
        for url in stubs.values():
            http.post(f"{url}/_calls/reset").raise_for_status()
        expected_upstream: dict[str, int] = {"a": 0, "b": 0}

        for key, client in clients.items():
            ws = client.ws
            _, response = client.call("list_ready_work")
            expected_upstream[key] += 1
            items = ((response.json().get("result") or {}).get("structuredContent") or {}).get("items") or []
            got = [(i.get("work_id"), i.get("title")) for i in items]
            want = [(wid, ws.title(wid)) for wid in ws.ready_ids]
            self.report.record(f"isolation.own-{key}", f"workspace {key.upper()} lists only its own work",
                               [] if got == want else [f"listed {got}, expected {want}"])
            _, response = client.call("describe_work", {"work_id": 1})
            expected_upstream[key] += 1
            item = ((response.json().get("result") or {}).get("structuredContent") or {}).get("item") or {}
            self.report.record(f"isolation.own-{key}", f"workspace {key.upper()} reads its own item 1",
                               [] if item.get("title") == ws.title(1) else [f"item 1 is {item!r}"])

        # Item 3 exists only in A: B's caller must not see it.
        _, response = clients["b"].call("describe_work", {"work_id": 3})
        expected_upstream["b"] += 1
        self.report.record("isolation.cross-read", "workspace B cannot read A's item 3",
                           tool_error_errors(response.json().get("result"), "item-not-found"))

        # Each workspace's assertion at the other workspace's edge.
        for source, target in (("a", "b"), ("b", "a")):
            token_ws = clients[source].ws
            edge = clients[target]
            for method, params in (
                ("tools/list", None),
                ("tools/call", {"name": "list_ready_work"}),
                ("tools/call", {"name": "describe_work", "arguments": {"work_id": 1}}),
            ):
                rpc_id, response = edge.request(method, params, token=edge.signer.mint(token_ws))
                label = f"{source.upper()}'s assertion at {target.upper()}'s edge: {method} {(params or {}).get('name', '')}".rstrip()
                self.error(f"isolation.cross-edge-{source}{target}", label, response,
                           http_status=401, code=-32001, definition="Error",
                           rpc_id=rpc_id, pre_parse=True)

        a, b = clients["a"], clients["b"]
        forged = [
            ("assertion naming workspace B, A's repository",
             a.signer.mint(a.ws, workspace_id=b.ws.workspace_id)),
            ("assertion naming B's repository", a.signer.mint(a.ws, repo_ids=[b.ws.repo_id])),
            ("assertion naming both repositories",
             a.signer.mint(a.ws, repo_ids=[a.ws.repo_id, b.ws.repo_id])),
            ("assertion with an authority outside this surface",
             a.signer.mint(a.ws, authorities=["work:read", "work:admin"])),
        ]
        for label, token in forged:
            rpc_id, response = a.request("tools/call", {"name": "list_ready_work"}, token=token)
            self.error("isolation.forged", f"edge A refuses an {label}", response,
                       http_status=401, code=-32001, definition="Error",
                       rpc_id=rpc_id, pre_parse=True)

        # Routing hints in headers change nothing: the edge serves its own
        # workspace, from the assertion's repository only.
        _, response = a.call("list_ready_work", extra={
            "X-Vuoro-Workspace": b.ws.workspace_id,
            "X-Upstream-Service": "stub-b",
            "X-Forwarded-Host": "stub-b",
        })
        expected_upstream["a"] += 1
        items = ((response.json().get("result") or {}).get("structuredContent") or {}).get("items") or []
        got = [i.get("title") for i in items]
        want = [a.ws.title(w) for w in a.ws.ready_ids]
        self.report.record("isolation.header-injection", "routing headers do not move A's call",
                           [] if got == want else [f"listed {got}, expected {want}"])

        # The upstream side: every invocation each runtime saw.
        for key, url in stubs.items():
            ws = clients[key].ws
            calls = http.get(f"{url}/_calls").json()["calls"]
            problems = []
            if len(calls) != expected_upstream[key]:
                problems.append(f"{len(calls)} upstream invocations, expected {expected_upstream[key]}")
            for call in calls:
                if not call.get("verified") or call.get("workspace_id") != ws.workspace_id:
                    problems.append(f"an invocation from workspace {call.get('workspace_id')!r}: {call!r}")
                elif call.get("repo_id") != ws.repo_id:
                    problems.append(f"an invocation for repository {call.get('repo_id')!r}")
                elif call.get("refusal"):
                    problems.append(f"the upstream had to refuse an invocation: {call['refusal']}")
                elif ENFORCE_EDGE_PROOF_AND_REPLAY and not call.get("edge_proof_present"):
                    problems.append(f"an upstream invocation without an edge proof: {call!r}")
            self.report.record(f"isolation.upstream-{key}",
                               f"runtime {key.upper()} saw only workspace {key.upper()}'s verified calls", problems)


def _wait_ready(url: str, deadline: float) -> None:
    last = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError as error:
            last = error
        time.sleep(0.5)
    raise SystemExit(f"{url} did not become ready: {last!r}")


def check(args: argparse.Namespace) -> int:
    schema = Schema()
    report = Report()
    signer = Signer(Path(args.state) / "signer" / "private.pem")
    edges = {"a": args.edge_a, "b": args.edge_b}
    stubs = {"a": args.stub_a, "b": args.stub_b}
    deadline = time.monotonic() + args.ready_timeout
    for url in edges.values():
        _wait_ready(f"{url.rstrip('/')}/health/live", deadline)
    for url in stubs.values():
        _wait_ready(f"{url.rstrip('/')}/health", deadline)
    clients = {key: StrictClient(url, signer, WORKSPACES[key]) for key, url in edges.items()}
    checks = Checks(schema, report)
    print(f"MCP {PROTOCOL_VERSION} strict client; schema {SCHEMA_SOURCE}")
    print("\n== conformance (workspace A's edge) ==")
    checks.conformance(clients["a"])
    print("\n== D-044 isolation (two workspaces, two edges) ==")
    checks.isolation(clients, stubs)
    return report.finish()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup")
    setup_parser.add_argument("state")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--state", required=True)
    for name in ("edge-a", "edge-b", "stub-a", "stub-b"):
        check_parser.add_argument(f"--{name}", required=True)
    check_parser.add_argument("--ready-timeout", type=float, default=60)
    args = parser.parse_args(argv)
    if args.command == "setup":
        setup(Path(args.state))
        return 0
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
