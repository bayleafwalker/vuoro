#!/usr/bin/env python3
"""Prove the v4 reference profile serves the same catalog as v3, where the wheels are.

The counterpart to ``scripts/validate_released_catalog_composition.py``, which
does the same thing for v3 by calling the four owner registrations directly.
This one goes through the v4 path -- support manifest, profile lock, uniform
construction -- and asserts the result is the same object: same operation count,
same per-domain counts, and the same catalog revision, byte for byte.

That last assertion is the one that matters, and it is worth being precise about
why. ``CatalogRegistry.revision`` is a sha256 over the registered operation
definitions plus resource kinds and observation transports; no manifest, lock,
pin or profile is an input to it. It is served as an ETag and on every result,
and a mismatch is rejected ``409 stale-catalog`` before an operation is even
resolved -- so a revision change is not a cache miss, it is a fleet-wide
cutover. A migration that rebinds the same adapters must therefore be invisible
on the wire, and this script is where that stops being an argument.

Run it where the pinned owner wheels are installed; it opens no database, and
that bound is worth stating because it cost a CI failure to learn.
``build`` is not database-free for every owner -- sprintctl's
``pg.get_connection`` calls ``psycopg.connect`` eagerly -- so a gate that must
not require Postgres cannot construct real applications. It therefore injects
stub applications and calls the real ``register`` for each binding: same
bindings, same order, same entrypoints resolved, and the served catalog is
unaffected because the revision digests operation definitions and a handler's
application is not one. What this proves is the catalog; what it does not prove
is that every owner constructor accepts what the profile pins, which only a
deployment with a database can.

``tests/test_composition_v4.py::test_v3_reference_composition_migrates_losslessly``
proves the composition path is revision-neutral without the wheels at all.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from vuoro_service.catalog import CatalogRegistry
from vuoro_service.composition_v4 import CompositionProfile, SupportManifest
from vuoro_service.composition_v4_runtime import compose
from vuoro_service.composition_v4_validator import violations


ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "packages/vuoro-service/composition"

EXPECTED_TOTAL = 87
EXPECTED_REVISION = "9a7621e0ab5b3765be162b6ae0fcdbf06c90bc66572873ef02d7ff5f3f14d4fd"
EXPECTED_DOMAIN_COUNTS = {"work": 46, "execution": 26, "knowledge": 10, "audit": 5}

# The two rule 3 violations the migrated profile is known to carry: v3 pins a
# catalog-metadata digest for work and execution and none for knowledge or
# audit. Listed rather than tolerated wholesale, so a *new* violation still
# fails this script.
KNOWN_GAPS = {
    "rule 3: audit/v1 declares operation-hashes conformance and audit-adapter "
    "records no operation_hashes",
    "rule 3: knowledge/v1 declares operation-hashes conformance and "
    "knowledge-adapter records no operation_hashes",
}

# Deployment supplies these. Nothing here connects, but a value still has to be
# *syntactically* what its setting is -- ActionQ rejects a schema name that is
# not a Postgres identifier before it ever opens a connection. Keyed on the
# setting name, which is Vuoro's own vocabulary from the profile, not on the
# owner: this script still does not know which owner wants which.
PLACEHOLDERS = {
    "dsn": "postgresql://validate-only/invalid",
    "schema": "validate_only",
    "repository_id": "validate-only",
}
DEFAULT_PLACEHOLDER = "validate-only"


class WorkStub:
    """The one application this gate cannot construct for real.

    Three of the four adapters build lazily and are constructed here exactly as
    a deployment would. sprintctl's ``pg.get_connection`` calls
    ``psycopg.connect`` in the constructor, so building a work application needs
    a live database, which this gate deliberately does not have. The stub is the
    same one ``scripts/validate_released_catalog_composition.py`` uses on the v3
    side, and it carries the single attribute the released work adapter reads
    while registering.

    Naming one owner here is what a per-provider conformance harness is for; the
    composer and the service package still name none.
    """

    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return False


#: adapter id -> the application to use instead of ``build``. Keyed on the
#: profile's own adapter id, and deliberately a listing of exceptions rather
#: than a blanket stub: everything absent from it is constructed for real.
OVERRIDDEN = {"work-catalog": WorkStub}


def main() -> int:
    manifest = SupportManifest.load(COMPOSITION / "support-manifest.json")
    profile = CompositionProfile.load(COMPOSITION / "profiles/shared.json")

    unexpected = set(violations(profile, manifest, root=ROOT)) - KNOWN_GAPS
    if unexpected:
        print("FAILED: profile violations beyond the known gaps:")
        for item in sorted(unexpected):
            print(f"  {item}")
        return 1

    environ = {
        variable: PLACEHOLDERS.get(setting, DEFAULT_PLACEHOLDER)
        for adapter in profile.adapters
        for setting, variable in adapter.runtime_settings.items()
    }
    composed = compose(
        profile, manifest,
        environ=environ,
        environment_name="validate",
        environment_class="development",
        registry=CatalogRegistry(),
        application_override=lambda adapter, runtime: (
            OVERRIDDEN[adapter.adapter_id]() if adapter.adapter_id in OVERRIDDEN else None
        ),
    )
    catalog = composed.registry.catalog().model_dump(mode="json")

    counts: dict[str, int] = {}
    for operation in catalog["operations"]:
        counts[operation["owning_domain"]] = counts.get(operation["owning_domain"], 0) + 1

    failures = []
    if len(catalog["operations"]) != EXPECTED_TOTAL:
        failures.append(f"operation count {len(catalog['operations'])} != {EXPECTED_TOTAL}")
    if counts != EXPECTED_DOMAIN_COUNTS:
        failures.append(f"per-domain counts {counts} != {EXPECTED_DOMAIN_COUNTS}")
    if composed.revision != EXPECTED_REVISION:
        failures.append(
            f"catalog revision {composed.revision} != {EXPECTED_REVISION}; the v4 profile "
            "would move every client's cached revision and trip stale-catalog fleet-wide"
        )
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(json.dumps({
        "profile": profile.profile,
        "profile_sha256": profile.profile_sha256,
        "operations": len(catalog["operations"]),
        "revision": composed.revision,
        "composed": [item.capability_id for item in composed.composed],
        "bound_not_composed": sorted(
            profile.bound_capabilities - {item.capability_id for item in composed.composed}
        ),
        "known_gaps": sorted(KNOWN_GAPS),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
