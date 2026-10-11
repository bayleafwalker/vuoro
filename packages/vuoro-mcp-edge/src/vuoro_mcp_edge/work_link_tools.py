"""Read-only work-link handoff tools over Sprintctl's continuation delta."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from typing import Any

from .errors import WorkSourceUnavailable
from .read_tools import CONTINUATION_DELTA, _OwnerReads
from .runs import RUN_ID, UnavailableRunRegistry, binding_for
from .toolsets import ToolFailure, ToolSet, ToolsetContext, ToolSpec
from .work_source import ForwardedIdentity

LINK_SCHEMA = "vuoro-work-link/v1"
DELTA_SCHEMA = "sprintctl-work-continuation-delta/v1"
URI_PREFIX = "vuoro-work://link/v1/"
MAX_URI_BYTES = 4096
_SAFE_ID = re.compile(r"[^\x00-\x1f\x7f]{1,256}(?![\s\S])")
_HEX = re.compile(r"[0-9a-f]{64}(?![\s\S])")
_SHA = re.compile(r"sha256:[0-9a-f]{64}(?![\s\S])")
_CHECKPOINT_FIELDS = frozenset(
    {
        "repo_id",
        "item_id",
        "work_edit_revision",
        "status_revision",
        "current_release_digest",
        "current_decision_digest",
        "current_reference_set_digest",
    }
)
_BASE64URL = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_MAKE = {
    "name": "make_work_link",
    "title": "Make a portable work handoff link",
    "description": (
        "Read one explicitly named work item through the current caller's owned run, "
        "then return a canonical copyable Vuoro locator. The link carries no authority; "
        "it is never resolved over the network and authorizes no action."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "pattern": RUN_ID.pattern},
            "item_id": {"type": "integer", "minimum": 1},
        },
        "required": ["run_id", "item_id"],
        "additionalProperties": False,
    },
    "annotations": _READ_ONLY,
}
_READ = {
    "name": "read_work_link",
    "title": "Read a portable work handoff link",
    "description": (
        "Read the linked work item through this caller's own successor run and the "
        "existing continuation-delta owner operation. The locator is parsed locally, "
        "never fetched. Register the successor run separately with the link's "
        "predecessor_run_id; this tool does not register runs or mutate owner state."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "pattern": RUN_ID.pattern},
            "work_link": {"type": "string", "minLength": 1, "maxLength": MAX_URI_BYTES},
        },
        "required": ["run_id", "work_link"],
        "additionalProperties": False,
    },
    "annotations": _READ_ONLY,
}
_INSPECT = {
    "name": "inspect_work_link",
    "title": "Inspect a work link for successor setup",
    "description": (
        "Parse the canonical locator locally and confirm that its workspace and "
        "repository match this request's current identity and selected repository. "
        "Returns only the predecessor run and item identifiers needed to register "
        "a successor. These are untrusted locator inputs; owner facts remain unknown, "
        "and the result authorizes no action. The locator is never fetched."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "work_link": {"type": "string", "minLength": 1, "maxLength": MAX_URI_BYTES},
        },
        "required": ["work_link"],
        "additionalProperties": False,
    },
    "annotations": _READ_ONLY,
}


def _fail_arguments() -> ToolFailure:
    return ToolFailure(
        "invalid-arguments", "give an exact owned run and work-link input"
    )


def _parse_make(args: dict[str, Any]) -> dict[str, Any]:
    if (
        set(args) != {"run_id", "item_id"}
        or type(args.get("run_id")) is not str
        or RUN_ID.fullmatch(args["run_id"]) is None
        or type(args.get("item_id")) is not int
        or args["item_id"] < 1
    ):
        raise _fail_arguments()
    return args


def _parse_read(args: dict[str, Any]) -> dict[str, Any]:
    if (
        set(args) != {"run_id", "work_link"}
        or type(args.get("run_id")) is not str
        or RUN_ID.fullmatch(args["run_id"]) is None
        or type(args.get("work_link")) is not str
        or not args["work_link"]
        or len(args["work_link"]) > MAX_URI_BYTES
    ):
        raise _fail_arguments()
    try:
        args["_link"] = decode_work_link(args["work_link"])
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise ToolFailure(
            "invalid-work-link", "the work link is not a canonical Vuoro locator"
        ) from None
    return args


def _parse_inspect(args: dict[str, Any]) -> dict[str, Any]:
    if (
        set(args) != {"work_link"}
        or type(args.get("work_link")) is not str
        or not args["work_link"]
        or len(args["work_link"]) > MAX_URI_BYTES
    ):
        raise _fail_arguments()
    try:
        args["_link"] = decode_work_link(args["work_link"])
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise ToolFailure(
            "invalid-work-link", "the work link is not a canonical Vuoro locator"
        ) from None
    return args


def _checkpoint_is_valid(checkpoint: Any) -> bool:
    if type(checkpoint) is not dict or set(checkpoint) != _CHECKPOINT_FIELDS:
        return False
    return (
        type(checkpoint.get("repo_id")) is str
        and _SAFE_ID.fullmatch(checkpoint["repo_id"]) is not None
        and type(checkpoint.get("item_id")) is int
        and checkpoint["item_id"] > 0
        and type(checkpoint.get("work_edit_revision")) is str
        and _SAFE_ID.fullmatch(checkpoint["work_edit_revision"]) is not None
        and type(checkpoint.get("status_revision")) is str
        and _SAFE_ID.fullmatch(checkpoint["status_revision"]) is not None
        and (
            checkpoint["current_release_digest"] is None
            or (
                type(checkpoint["current_release_digest"]) is str
                and _HEX.fullmatch(checkpoint["current_release_digest"]) is not None
            )
        )
        and (
            checkpoint["current_decision_digest"] is None
            or (
                type(checkpoint["current_decision_digest"]) is str
                and _SHA.fullmatch(checkpoint["current_decision_digest"]) is not None
            )
        )
        and (
            checkpoint["current_reference_set_digest"] is None
            or (
                type(checkpoint["current_reference_set_digest"]) is str
                and _SHA.fullmatch(checkpoint["current_reference_set_digest"])
                is not None
            )
        )
    )


def _validate_link(link: Any) -> dict[str, Any]:
    required = {"schema", "workspace_id", "repo_id", "item_id", "predecessor_run_id"}
    if (
        type(link) is not dict
        or not required.issubset(link)
        or set(link) - required - {"owner_checkpoint"}
    ):
        raise ValueError("work link has an invalid object shape")
    if (
        link["schema"] != LINK_SCHEMA
        or type(link["workspace_id"]) is not str
        or _SAFE_ID.fullmatch(link["workspace_id"]) is None
        or type(link["repo_id"]) is not str
        or _SAFE_ID.fullmatch(link["repo_id"]) is None
        or type(link["item_id"]) is not int
        or link["item_id"] < 1
        or type(link["predecessor_run_id"]) is not str
        or RUN_ID.fullmatch(link["predecessor_run_id"]) is None
    ):
        raise ValueError("work link contains invalid identifiers")
    if "owner_checkpoint" in link:
        checkpoint = link["owner_checkpoint"]
        if (
            not _checkpoint_is_valid(checkpoint)
            or checkpoint["repo_id"] != link["repo_id"]
            or checkpoint["item_id"] != link["item_id"]
        ):
            raise ValueError("work link checkpoint does not match its item")
    return link


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def encode_work_link(link: Mapping[str, Any]) -> str:
    normalized = _validate_link(dict(link))
    payload = (
        base64.urlsafe_b64encode(_canonical_json(normalized))
        .rstrip(b"=")
        .decode("ascii")
    )
    uri = URI_PREFIX + payload
    if len(uri.encode("ascii")) > MAX_URI_BYTES:
        raise ToolFailure(
            "work-link-too-large", "the canonical work link exceeds 4096 bytes"
        )
    return uri


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def decode_work_link(uri: str) -> dict[str, Any]:
    if type(uri) is not str or not uri.startswith(URI_PREFIX):
        raise ValueError("noncanonical locator prefix")
    try:
        encoded_bytes = uri.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("locator must be ASCII") from error
    if len(encoded_bytes) > MAX_URI_BYTES:
        raise ValueError("locator exceeds size limit")
    encoded = uri[len(URI_PREFIX) :]
    if not encoded or any(char not in _BASE64URL for char in encoded):
        raise ValueError("payload must be unpadded base64url")
    raw = base64.b64decode(
        encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
    )
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != encoded:
        raise ValueError("payload encoding is not canonical")
    value = json.loads(
        raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object
    )
    if _canonical_json(value) != raw:
        raise ValueError("payload JSON is not canonical")
    return _validate_link(value)


def _validate_owner_delta(
    delta: Any,
    *,
    repo_id: str,
    item_id: int,
    run_id: str,
    predecessor_run_id: str | None = None,
) -> dict[str, Any]:
    if type(delta) is not dict or delta.get("schema") != DELTA_SCHEMA:
        raise ToolFailure(
            "owner-response-invalid", "the work owner returned an unsupported delta"
        )
    auth = delta.get("authorization_scope")
    current = delta.get("current")
    work = delta.get("work")
    if (
        type(auth) is not dict
        or auth.get("run_id") != run_id
        or auth.get("repo_id") != repo_id
        or auth.get("run_owned") is not True
        or type(current) is not dict
        or set(current) != _CHECKPOINT_FIELDS
        or not _checkpoint_is_valid(current)
        or current.get("repo_id") != repo_id
        or current.get("item_id") != item_id
        or type(work) is not dict
        or type(work.get("item_id")) is not int
        or work.get("item_id") != item_id
        or delta.get("authorizes_action") is not False
    ):
        raise ToolFailure(
            "owner-response-invalid",
            "the owner delta does not bind this run, repository and item",
        )
    if (
        predecessor_run_id is not None
        and delta.get("predecessor_run_id") != predecessor_run_id
    ):
        raise ToolFailure(
            "run-predecessor-mismatch",
            "the successor run is not linked to this work handoff",
        )
    return current


def _current_workspace(forwarded: ForwardedIdentity) -> str:
    identity = forwarded.identity
    if identity is None or not identity.principal_id or not identity.workspace_id:
        raise ToolFailure(
            "run-binding-unavailable",
            "the caller's principal and workspace are unavailable",
        )
    return identity.workspace_id


async def _invoke_delta(
    owner: _OwnerReads, args: dict[str, Any], caller: ForwardedIdentity
) -> dict[str, Any]:
    """Use the existing owner operation and require its exact response envelope."""
    try:
        value = await owner.source._invoke(CONTINUATION_DELTA, args, caller)
    except WorkSourceUnavailable as error:
        if error.code == "authority-required":
            raise ToolFailure(
                "authority-required", "the current caller lacks an owner read authority"
            ) from error
        if error.code in {"item-not-found", "run-not-found"}:
            raise ToolFailure(
                "work-not-found", "no authorized owner record was found"
            ) from error
        if error.code == "stale-cursor":
            raise ToolFailure(
                "stale-cursor", "owner facts changed; restart this bounded read"
            ) from error
        raise ToolFailure(
            "owner-read-unavailable", "the work owner could not supply this read"
        ) from error
    if (
        type(value) is not dict
        or set(value) != {"repo_id", "delta"}
        or value.get("repo_id") != caller.repo_id
        or type(value.get("delta")) is not dict
        or value["delta"].get("schema") != DELTA_SCHEMA
    ):
        raise ToolFailure(
            "owner-response-invalid", "the work owner returned an inconsistent read"
        )
    return value["delta"]


def build_toolset(context: ToolsetContext) -> ToolSet | None:
    runs = context.runs
    if isinstance(runs, UnavailableRunRegistry):
        return None
    continuation = bool(getattr(runs, "supports_continuation", False))
    detect_continuation = (
        None if continuation else getattr(runs, "continuation_available", None)
    )
    if not continuation and detect_continuation is None:
        return None
    owner = _OwnerReads(context.work_source)

    async def available() -> bool:
        if not await owner.available(CONTINUATION_DELTA):
            return False
        return continuation or bool(await detect_continuation())

    async def describe_make():
        return _MAKE if await available() else None

    async def describe_read():
        return _READ if await available() else None

    async def describe_inspect():
        return _INSPECT if await available() else None

    async def make(
        args: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        workspace_id = _current_workspace(forwarded)
        binding = binding_for(forwarded)
        await runs.resolve(args["run_id"], binding, forwarded=forwarded)
        envelope = await _invoke_delta(
            owner,
            {"run_id": args["run_id"], "item_id": args["item_id"], "limit": 1},
            forwarded,
        )
        checkpoint = _validate_owner_delta(
            envelope,
            repo_id=forwarded.repo_id,
            item_id=args["item_id"],
            run_id=args["run_id"],
        )
        uri = encode_work_link(
            {
                "schema": LINK_SCHEMA,
                "workspace_id": workspace_id,
                "repo_id": forwarded.repo_id,
                "item_id": args["item_id"],
                "predecessor_run_id": args["run_id"],
                "owner_checkpoint": checkpoint,
            }
        )
        return {"work_link": uri}

    async def read(
        args: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        link = args["_link"]
        workspace_id = _current_workspace(forwarded)
        if link["workspace_id"] != workspace_id or link["repo_id"] != forwarded.repo_id:
            raise ToolFailure(
                "work-link-scope-mismatch",
                "the link belongs to another workspace or repository",
            )
        binding = binding_for(forwarded)
        await runs.resolve(args["run_id"], binding, forwarded=forwarded)
        owner_args: dict[str, Any] = {
            "run_id": args["run_id"],
            "item_id": link["item_id"],
            "limit": 1,
        }
        if "owner_checkpoint" in link:
            owner_args["checkpoint"] = link["owner_checkpoint"]
        delta = await _invoke_delta(owner, owner_args, forwarded)
        _validate_owner_delta(
            delta,
            repo_id=link["repo_id"],
            item_id=link["item_id"],
            run_id=args["run_id"],
            predecessor_run_id=link["predecessor_run_id"],
        )
        return delta

    async def inspect(
        args: dict[str, Any], forwarded: ForwardedIdentity
    ) -> dict[str, Any]:
        link = args["_link"]
        workspace_id = _current_workspace(forwarded)
        if link["workspace_id"] != workspace_id or link["repo_id"] != forwarded.repo_id:
            raise ToolFailure(
                "work-link-scope-mismatch",
                "the link belongs to another workspace or repository",
            )
        return {
            "predecessor_run_id": link["predecessor_run_id"],
            "item_id": link["item_id"],
            "owner_facts": "UNKNOWN",
            "authorizes_action": False,
        }

    return ToolSet(
        "work-link-read",
        (
            ToolSpec(
                "make_work_link",
                "read",
                _MAKE,
                _parse_make,
                make,
                describe=describe_make,
                required_authorities=frozenset({"work:read"}),
            ),
            ToolSpec(
                "read_work_link",
                "read",
                _READ,
                _parse_read,
                read,
                describe=describe_read,
                required_authorities=frozenset({"work:read"}),
            ),
            ToolSpec(
                "inspect_work_link",
                "read",
                _INSPECT,
                _parse_inspect,
                inspect,
                describe=describe_inspect,
                required_authorities=frozenset({"work:read"}),
            ),
        ),
    )
