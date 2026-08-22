"""The uniform construction protocol: how a v4 profile becomes a catalog.

Freeze §3.3. Every wheel provider is constructed the same way::

    build(runtime: RuntimeConfiguration) -> Application
    register(registry, application) -> None

and that is the whole interface between Vuoro and an owner. This module is the
composer that calls it, and its defining property is negative: **no owner name,
module path, constructor argument, registration convention or contract-name
literal appears here.** Everything specific comes from the profile.

That is the freeze's most load-bearing addition, and it is worth being precise
about what it buys, because the near miss is subtle. Deleting the four-domain
constant makes a fifth contract *declarable* -- a v4 profile could name
``federation/v1``, validate green, and register nothing at all, because v3's
composer hand-wires four domains by name and would simply not know about the
fifth. Uniform construction is what makes the fifth contract *composable*: the
composer iterates bindings it has never heard of and constructs each through
one protocol.

Compare what it replaces (``composition.py``): ``manifest.pin("work")`` with a
direct ``from sprintctl import pg``, an ``ActionQApplication(...)`` literal
constructor call, a hardcoded audit exception that string-matches
``auditctl.vuoro_adapter`` / ``VuoroAuditAdapter.register`` and bypasses
``_load_function`` entirely, and a literal ``domains = {...}``. Four owners,
four shapes, four places to edit for a fifth.

Nothing here is wired into ``create_composed_app``: v3 still composes the
service that runs. This composes a registry from a v4 profile, which is what
the equivalence proof compares against and what a candidate deployment will
call once the proof passes.

Where the owner-specific knowledge went
---------------------------------------

It did not evaporate; it moved out of Vuoro's service package and into adapter
modules the profile names -- for today's four owners, thin shims in
``vuoro_adapter_kit.adapters``. An owner whose wheel grows its own ``build``
needs no shim at all and is named directly by the profile. Either way the
service package holds no owner name, which is the property rule 7 checks and
the reason a fifth contract costs a profile edit rather than a service release.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import importlib
import inspect
from typing import Any, Callable

from vuoro_service.catalog import CatalogRegistry
from vuoro_service.composition_v4 import (
    Adapter,
    AuthorityBinding,
    CompositionProfile,
    CompositionV4Error,
    Provider,
    SupportManifest,
)


@dataclass(frozen=True)
class RuntimeConfiguration:
    """What deployment supplies to one adapter, declared rather than compiled in.

    The profile pins *which* settings an adapter needs and where each comes
    from (``runtime_settings``: setting name -> environment variable); the
    deployment supplies the values. Vuoro therefore never names a DSN or a
    schema variable in code, and adding a contract that needs a third setting
    is a profile edit.

    ``settings`` is deliberately opaque to this module. Interpreting it is the
    adapter's job -- that is what makes ``build`` uniform across an owner that
    wants a DSN and a schema, and one that wants a URL and a token.
    """

    capability_id: str
    environment_name: str
    environment_class: str
    settings: Mapping[str, str]

    def require(self, name: str) -> str:
        """The setting or a composition failure -- never a silent default.

        Adapters call this rather than ``settings[...]`` so a missing pin fails
        as a composition error naming the capability, not as a ``KeyError``
        inside owner code.
        """
        try:
            return self.settings[name]
        except KeyError:
            raise CompositionV4Error(
                f"{self.capability_id}: profile pins no runtime setting {name!r}"
            ) from None


@dataclass(frozen=True)
class ComposedCapability:
    """One binding, constructed and registered."""

    capability_id: str
    scope: tuple[str, str | None]
    provider_id: str
    adapter_id: str
    application: Any


@dataclass(frozen=True)
class ComposedCatalog:
    """The registry a profile produced, and the bindings that produced it."""

    registry: CatalogRegistry
    composed: tuple[ComposedCapability, ...]

    @property
    def revision(self) -> str:
        return self.registry.revision


def _entrypoint(module_name: str, attribute: str, label: str) -> Callable[..., Any]:
    """Resolve ``module:attribute``, allowing one level of class attribute.

    The dotted form exists because v3's audit adapter registers through an
    instance method (``VuoroAuditAdapter.register``) rather than a function, and
    v3 handled that with a hardcoded string comparison in service code. Naming
    the attribute path in the profile is the same expressiveness without the
    exception -- the composer resolves what the profile names and knows nothing
    about who wrote it.
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise CompositionV4Error(f"{label}: cannot import {module_name!r}") from error
    target: Any = module
    for part in attribute.split("."):
        target = getattr(target, part, None)
        if target is None:
            raise CompositionV4Error(
                f"{label}: {module_name} exposes no {attribute!r}"
            )
    if not callable(target):
        raise CompositionV4Error(f"{label}: {module_name}.{attribute} is not callable")
    return target


def satisfies_uniform_construction(adapter: Adapter) -> tuple[bool, str]:
    """Whether an adapter can be constructed through the one protocol.

    Signature-checked, not merely name-checked: an adapter exposing a ``build``
    that demands three positional arguments is not constructible by a composer
    that has exactly one thing to give it, and finding that out at deployment
    rather than at validation is the failure this is here to prevent.
    """
    if adapter.module is None:
        return False, f"adapter {adapter.adapter_id!r} declares no module to construct from"
    label = f"adapter {adapter.adapter_id!r}"
    try:
        build = _entrypoint(adapter.module, adapter.build or "", label)
        register = _entrypoint(adapter.module, adapter.register or "", label)
    except CompositionV4Error as error:
        return False, str(error)
    if not _accepts(build, 1):
        return False, f"{label}: build must accept one RuntimeConfiguration"
    if not _accepts(register, 2):
        return False, f"{label}: register must accept (registry, application)"
    return True, ""


def _accepts(function: Callable[..., Any], count: int) -> bool:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):  # pragma: no cover - builtins have no signature
        return True
    positional = [
        parameter for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    if any(
        parameter.kind is parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    ):
        return True
    required = [parameter for parameter in positional if parameter.default is parameter.empty]
    return len(required) <= count <= len(positional)


def runtime_configuration(
    binding: AuthorityBinding,
    adapter: Adapter,
    *,
    environ: Mapping[str, str],
    environment_name: str,
    environment_class: str,
) -> RuntimeConfiguration:
    missing = [
        variable for variable in adapter.runtime_settings.values() if not environ.get(variable)
    ]
    if missing:
        raise CompositionV4Error(
            f"{binding.capability_id}: deployment supplies no {sorted(missing)}"
        )
    return RuntimeConfiguration(
        capability_id=binding.capability_id,
        environment_name=environment_name,
        environment_class=environment_class,
        settings={
            name: environ[variable] for name, variable in adapter.runtime_settings.items()
        },
    )


def compose(
    profile: CompositionProfile,
    manifest: SupportManifest,
    *,
    environ: Mapping[str, str],
    environment_name: str,
    environment_class: str,
    registry: CatalogRegistry | None = None,
    application_factory: Callable[[Adapter, RuntimeConfiguration], Any] | None = None,
) -> ComposedCatalog:
    """Build and register every binding the profile declares.

    Bindings are composed in capability order so that a composition is
    reproducible; the catalog revision does not depend on it (``CatalogRegistry``
    sorts operations, resource kinds and transports before digesting), and that
    independence is a property worth relying on deliberately rather than
    accidentally -- it is half of why the migration can be lossless.

    A binding whose adapter declares no module is a provider Vuoro reaches over
    the network rather than in-process: there is nothing to import and nothing
    to register, and it is skipped rather than treated as an error.

    ``application_factory`` replaces the ``build`` call and nothing else -- same
    bindings, same order, same ``register``, and ``build`` is still resolved so
    a missing entrypoint still fails. It exists because ``build`` is not
    database-free for every owner: sprintctl's ``pg.get_connection`` opens a
    psycopg connection eagerly, so a gate that must not require Postgres cannot
    construct real applications. Proving the served catalog does not need them
    -- the revision is a digest over operation definitions, and a handler's
    application is not part of it -- so the proof injects stubs and says so,
    rather than the composer quietly tolerating a half-built application.
    """
    registry = registry if registry is not None else CatalogRegistry()
    composed: list[ComposedCapability] = []
    for binding in sorted(profile.bindings, key=lambda item: (item.capability_id, item.scope_kind, item.scope_instance or "")):
        # Resolving through the manifest keeps an unsupported capability from
        # being composed even when the profile is internally consistent.
        manifest.contract(binding.capability_id)
        adapter = profile.adapter(binding.adapter_id)
        if adapter.module is None:
            continue
        ok, complaint = satisfies_uniform_construction(adapter)
        if not ok:
            raise CompositionV4Error(complaint)
        runtime = runtime_configuration(
            binding, adapter,
            environ=environ,
            environment_name=environment_name,
            environment_class=environment_class,
        )
        label = f"{binding.capability_id} via adapter {adapter.adapter_id!r}"
        build = _entrypoint(adapter.module, adapter.build or "", label)
        register = _entrypoint(adapter.module, adapter.register or "", label)
        try:
            application = (
                build(runtime) if application_factory is None
                else application_factory(adapter, runtime)
            )
        except CompositionV4Error:
            raise
        except Exception as error:
            raise CompositionV4Error(f"{label}: build failed") from error
        try:
            register(registry, application)
        except Exception as error:
            raise CompositionV4Error(f"{label}: register failed") from error
        composed.append(ComposedCapability(
            capability_id=binding.capability_id,
            scope=binding.scope,
            provider_id=binding.provider_id,
            adapter_id=adapter.adapter_id,
            application=application,
        ))
    return ComposedCatalog(registry=registry, composed=tuple(composed))


def provider_for(profile: CompositionProfile, binding: AuthorityBinding) -> Provider:
    return profile.provider(binding.provider_id)


__all__ = [
    "ComposedCapability",
    "ComposedCatalog",
    "RuntimeConfiguration",
    "compose",
    "provider_for",
    "runtime_configuration",
    "satisfies_uniform_construction",
]
