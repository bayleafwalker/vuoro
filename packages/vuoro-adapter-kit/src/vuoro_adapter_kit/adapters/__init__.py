"""Uniform-construction shims for the wheel providers Vuoro composes.

Freeze §3.4: an adapter is a thin Vuoro translation layer that owns no
canonical state. Each module here exposes exactly two entrypoints::

    build(runtime: RuntimeConfiguration) -> Application
    register(registry, application) -> None

and nothing else. They live in the adapter kit rather than in
``vuoro_service`` on purpose: the service package must contain no owner name or
owner module path (rule 7), and moving the four owner-specific constructions
out of ``create_composed_app`` is what makes a fifth contract composable by
adding a module and a profile record instead of editing the composer.

Owner imports are deferred into ``build`` so that importing this package -- to
check that it satisfies the protocol, which the validator does -- does not
require every owner wheel to be installed.

What these shims are not: proof that the owner constructors accept what is
passed. That is release-environment evidence and lives in
``scripts/validate_released_*_adapter.py``, which runs where the pinned wheels
are actually installed. The tests here inject fake owner modules and prove the
half that is Vuoro's: that each shim satisfies the protocol, reads only what
the profile pinned, and delegates registration without interpreting it.
"""

from __future__ import annotations
