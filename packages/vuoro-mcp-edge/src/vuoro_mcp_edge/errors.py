"""The one failure type the work source raises, and its client-facing form.

Empty-because-the-authority-is-down must never be indistinguishable from
empty-because-there-is-no-ready-work.  Every failure to answer raises
`WorkSourceUnavailable`, and the protocol server turns it into a tool error.

No upstream text ever reaches the client.  `client_error` maps each known
code to a message written here; an unknown upstream code becomes
`upstream-rejected` with only the (length-capped) code carried alongside.
"""

from __future__ import annotations

import re

__all__ = [
    "LOCAL_MESSAGES",
    "NOT_FOUND_CODE",
    "WorkSourceUnavailable",
    "client_error",
    "is_not_found",
]

UPSTREAM_CODE_MAX_LENGTH = 64
_CODE_SHAPE = re.compile(r"^[A-Za-z0-9._:-]+$")

#: The one code that means "no such item", not "could not answer".
NOT_FOUND_CODE = "item-not-found"

#: The only messages a client sees for a work-source failure.
LOCAL_MESSAGES: dict[str, str] = {
    # Codes the runtime shell or sprintctl returns.
    "item-not-found": "no work item has that work_id",
    "postgres-runtime-unavailable": (
        "the work authority is unavailable; retry later"
    ),
    "stale-catalog": "the runtime catalog changed during the call; retry",
    "schema-validation-failed": "the runtime rejected the request arguments",
    "identity-required": "the runtime did not accept the caller's identity",
    "authority-required": "the caller lacks the authority this tool needs",
    # Codes assigned in this package.
    "catalog-mismatch": (
        "the runtime catalog does not advertise work.public.list-v1 and "
        "work.public.item-v1"
    ),
    "catalog-unavailable": "the runtime catalog could not be read",
    "transport-error": "the runtime shell could not be reached",
    "invalid-response": "the runtime returned a response outside the contract",
    "contract-violation": (
        "the runtime returned a record outside the public-work contract"
    ),
    "authority-unavailable": "the work authority reported it is not available",
    "upstream-mismatch": "the runtime returned a different item than requested",
}
_UPSTREAM_REJECTED_MESSAGE = "the runtime rejected the call"
_ALIASES = {"transport": "transport-error"}


class WorkSourceUnavailable(RuntimeError):
    """The runtime shell could not give a contract-conforming answer.

    `code` is the shell's invocation `error.code` when `upstream` is true,
    and otherwise a code assigned here.  `message` is for logs only.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        upstream: bool = False,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.upstream = upstream


def is_not_found(error: WorkSourceUnavailable) -> bool:
    """True for an ordinary not-found answer, never for an outage.

    A not-found is a normal contract response, not evidence the work source
    failed to answer, so it must not be logged or counted alongside
    genuine unavailability.
    """

    return _ALIASES.get(error.code, error.code) == NOT_FOUND_CODE


def client_error(error: WorkSourceUnavailable) -> dict[str, str]:
    """The `structuredContent.error` object for a failure: local text only."""

    code = _ALIASES.get(error.code, error.code)
    if code in LOCAL_MESSAGES:
        return {"code": code, "message": LOCAL_MESSAGES[code]}
    upstream_code = code[:UPSTREAM_CODE_MAX_LENGTH]
    if not _CODE_SHAPE.fullmatch(upstream_code):
        upstream_code = "unrecognized"
    return {
        "code": "upstream-rejected",
        "message": _UPSTREAM_REJECTED_MESSAGE,
        "upstream_code": upstream_code,
    }
