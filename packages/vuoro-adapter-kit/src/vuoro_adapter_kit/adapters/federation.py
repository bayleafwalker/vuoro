"""ActionQ `federation.resource/v1` behind the uniform construction protocol.

The iterative sibling of ``execution.py``: a second, separate release unit
from the frozen ``execution/v1`` adapter, staged so that once the operator
publishes a verified digest for the ActionQ wheel that carries it, binding it
is a one-record change to the profile rather than a source change here.
"""

from __future__ import annotations

from typing import Any


def build(runtime: Any) -> Any:
    from actionq.vuoro_federation import build as _build

    return _build(runtime)


def register(registry: Any, application: Any) -> None:
    from actionq.vuoro_federation import register as _register

    _register(registry, application)
