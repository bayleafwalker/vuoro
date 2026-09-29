"""Unit checks for scripts/settlement_scenario.py's transcript hygiene (agentops#2524).

The scenario itself runs in the `settlement-scenario` CI job; these pin the
parts that protect a live run's transcript, which is committed to this public
repository: listings keep only the run's own items, and credential files are
written 0600 from the start.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "settlement_scenario", ROOT / "scripts/settlement_scenario.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scenario = _load()

FOREIGN_TITLE = "operator's real item -- must never reach the transcript"


def test_list_ready_work_keeps_only_run_items() -> None:
    answer = {
        "authority": "sprintctl",
        "items": [
            {"work_id": 7, "title": FOREIGN_TITLE, "status": "pending"},
            {"work_id": 41, "title": "M1-4 scenario X", "status": "pending"},
            {"work_id": 8, "title": FOREIGN_TITLE, "status": "pending"},
        ],
    }
    scoped = scenario.scope_to_run_items(answer, frozenset({41, 42}))
    assert [item["work_id"] for item in scoped["items"]] == [41]
    assert scoped["omitted_foreign_items"] == {"items": 2}
    assert FOREIGN_TITLE not in json.dumps(scoped)
    assert scoped["authority"] == "sprintctl"
    # The owner's answer itself is untouched: expectations read it.
    assert len(answer["items"]) == 3


def test_next_work_lists_are_scoped_and_nested_lists_too() -> None:
    answer = {
        "ready_items": [
            {"id": 3, "title": FOREIGN_TITLE, "sprint_id": 9},
            {"id": 42, "title": "M1-4 scenario Y", "sprint_id": 1},
        ],
        "blocked_items": [{"id": 5, "title": FOREIGN_TITLE}],
        "context": {"active_items": [{"id": 6, "title": FOREIGN_TITLE}]},
        "dispatch_reason": "ready-items-available",
    }
    scoped = scenario.scope_to_run_items(answer, frozenset({41, 42}))
    assert [item["id"] for item in scoped["ready_items"]] == [42]
    assert scoped["blocked_items"] == []
    assert scoped["context"]["active_items"] == []
    assert scoped["omitted_foreign_items"] == {"ready_items": 1, "blocked_items": 1}
    assert scoped["context"]["omitted_foreign_items"] == {"active_items": 1}
    assert FOREIGN_TITLE not in json.dumps(scoped)
    assert scoped["dispatch_reason"] == "ready-items-available"


def test_non_listing_answers_pass_through() -> None:
    lease = {"lease": {"lease_id": "lease_X", "item_id": 41}, "resumed": False}
    checks = {"checks": [{"name": "scenario-check", "status": "passed"}]}
    assert scenario.scope_to_run_items(lease, frozenset()) == lease
    assert scenario.scope_to_run_items(checks, frozenset()) == checks


def test_private_files_are_0600_from_creation(tmp_path: Path) -> None:
    target = tmp_path / "grant.json"
    scenario.write_private(target, '{"refresh_token": "x"}')
    scenario.write_private(target, '{"refresh_token": "y"}')  # a refresh rewrites it
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"refresh_token": "y"}
    assert [path.name for path in tmp_path.iterdir()] == ["grant.json"]


def _ints(value):
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        yield value
    elif isinstance(value, dict):
        for entry in value.values():
            yield from _ints(entry)
    elif isinstance(value, list):
        for entry in value:
            yield from _ints(entry)


def test_check_details_never_carry_foreign_ids(tmp_path: Path, capsys) -> None:
    """A seeded foreign item (999) must not reach any expectation detail.

    The review of vuoro#152 found readiness checks recording the full
    `list_ready_work` id list (for example [1, 3, 4, 5]) in their detail.
    """

    transcript = scenario.Transcript(tmp_path / "t.jsonl")
    run_items = frozenset({1, 2, 3})
    expect = scenario.Expectations(
        transcript, scope=lambda detail: scenario.scope_detail(detail, run_items)
    )
    foreign = 999
    expect.check("takeover", "ready ids", True, [1, foreign, 3])
    expect.check("takeover", "ready ids, failing", False, [foreign, 2])
    expect.check("takeover", "listing", True,
                 {"items": [{"work_id": foreign, "title": FOREIGN_TITLE}, {"work_id": 2}]})
    expect.check("takeover", "no detail", True, None)
    entries = transcript.read()
    details = [entry["detail"] for entry in entries]
    assert details[0] == {"run_items": [1, 3], "omitted_foreign_items": 1}
    assert details[1] == {"run_items": [2], "omitted_foreign_items": 1}
    assert all(foreign not in set(_ints(detail)) for detail in details)
    assert FOREIGN_TITLE not in transcript.path.read_text()
    assert str(foreign) not in capsys.readouterr().out


def test_live_mode_refuses_to_start_without_the_sprintctl_wheel(tmp_path, monkeypatch, capsys):
    """Live cleanup closes the sprint with sprintctl's contracts; fail before spending credentials."""

    import pytest

    module = _load()
    for name in ("a.json", "b.json", "pat"):
        (tmp_path / name).write_text("{}")
    argv = ["run", "--mode", "live", "--token-a", str(tmp_path / "a.json"),
            "--token-b", str(tmp_path / "b.json"), "--pat-file", str(tmp_path / "pat")]
    real_find_spec = module.importlib.util.find_spec
    monkeypatch.setattr(module.importlib.util, "find_spec",
                        lambda name, *a: None if name == "sprintctl" else real_find_spec(name, *a))
    with pytest.raises(SystemExit) as refused:
        module.main(argv)
    assert refused.value.code == 2
    assert "pinned sprintctl wheel" in capsys.readouterr().err
