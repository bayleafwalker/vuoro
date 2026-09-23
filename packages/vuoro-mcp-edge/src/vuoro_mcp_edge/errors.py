"""The one failure type the work source raises.

Empty-because-the-authority-is-down must never be indistinguishable from
empty-because-there-is-no-ready-work.  Every failure to answer raises
`WorkSourceUnavailable`, and the protocol server turns it into a tool error.
"""

from __future__ import annotations

__all__ = ["WorkSourceUnavailable"]


class WorkSourceUnavailable(RuntimeError):
    """The runtime shell could not give a contract-conforming answer.

    `code` preserves the shell's invocation `error.code` where there was one
    (for example `postgres-runtime-unavailable`, `item-not-found`,
    `authority-required`), and is otherwise a locally assigned code:
    `transport-error`, `invalid-response`, `catalog-unavailable`,
    `catalog-mismatch`, `contract-violation`, `authority-unavailable`.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code
