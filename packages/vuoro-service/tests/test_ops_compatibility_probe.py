"""The compatibility probe never silently skips a pinned domain."""

from __future__ import annotations

import json

from vuoro_service.ops.probes import compatibility


def test_a_domain_without_a_dsn_is_reported_not_checked() -> None:
    observation = compatibility.observe({"VUORO_OPS_PINS": json.dumps({})})
    assert observation["domains"] == {}
    assert observation["unknowns"] == [
        "domain-not-checked:work", "domain-not-checked:audit", "domain-not-checked:knowledge",
    ]


def test_an_unreachable_knowledge_store_is_incompatible_not_skipped(tmp_path) -> None:
    observation = compatibility.observe({
        "VUORO_OPS_PINS": json.dumps({}),
        "VUORO_OPS_KNOWLEDGE_DSN": f"host={tmp_path / 'nosock'} dbname=none connect_timeout=1",
        "VUORO_OPS_KNOWLEDGE_ROLE": "knowledge_runtime",
    })
    assert observation["unknowns"] == ["domain-not-checked:work", "domain-not-checked:audit"]
    knowledge = observation["domains"]["knowledge"]
    assert knowledge["compatible"] is False and knowledge["reason"] == "probe-error"
