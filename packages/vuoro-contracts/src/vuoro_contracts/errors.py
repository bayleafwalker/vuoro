"""Stable safe boundary errors; never format an input value into a message."""
from __future__ import annotations

class ContractError(ValueError):
    def __init__(self, code: str, path: tuple[str | int, ...] = ()) -> None:
        self.code = code
        self.path = path
        super().__init__(code)
