"""Provider-neutral lease contract (LTD §7.1; INV-L1 and INV-L2).

Bindings normalize owner codes here; scenarios only speak contract codes.
Time hooks are test-only and never form part of the published operation API.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Claim:
    claim_id: str
    subject: str
    holder: str


@dataclass(frozen=True)
class Outcome:
    settled: bool
    code: str | None = None


class Refused(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class LeaseProvider(Protocol):
    def new_subject(self) -> str: ...
    def claim(self, subject: str, holder: str) -> Claim: ...
    def heartbeat(self, handle: Claim, holder: str) -> None: ...
    def report_outcome(self, handle: Claim, holder: str, outcome: object) -> Outcome: ...
    def make_stale(self, handle: Claim) -> None: ...
    def is_stale(self, handle: Claim) -> bool: ...
    def retained_outcomes(self, subject: str) -> list[dict]: ...
    def current_claim_id(self, subject: str) -> str | None: ...
    # Read-only observation for the no-background-effect invariant.
