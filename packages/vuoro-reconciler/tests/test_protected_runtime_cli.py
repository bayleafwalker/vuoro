"""Fixed CLI route and diagnostic gates; fixture DATA confers no authority."""

import json
from types import SimpleNamespace

import pytest

from vuoro_reconciler import protected_runtime_cli as module
from vuoro_reconciler.protected_copy_codec import _canonical
from vuoro_reconciler.protected_effects import AcceptedPageData, AcceptedMetadata
from vuoro_reconciler.protected_executor import ExecutionResult

ACCEPTANCE = "iac_" + "a" * 32


def fixture_runtime(monkeypatch, status="APPLIED", close_failure=False):
    seen = []

    async def run(identifier):
        seen.append(("execute", identifier))
        return ExecutionResult(status, "fixture-result", "b" * 64, None)

    async def listing(cursor, limit):
        seen.append(("list", cursor, limit))
        return AcceptedPageData(
            _canonical(
                {
                    "authority_id": "fixture",
                    "workspace_id": "fixture",
                    "repo_id": "fixture",
                }
            ),
            (
                AcceptedMetadata(
                    ACCEPTANCE,
                    "ipr_" + "c" * 32,
                    "sha256:" + "d" * 64,
                    "iar_" + "e" * 32,
                    "2026-10-14T00:00:00Z",
                ),
            ),
            None,
        )

    class Runtime:
        executor = SimpleNamespace(
            run=run, ledger=SimpleNamespace(list_accepted=listing)
        )

        def __enter__(self):
            seen.append("enter")
            return self

        def __exit__(self, *args):
            seen.append("close")
            if close_failure:
                raise OSError("sensitive fixture credential path")

    def open_runtime(identifier):
        seen.append(("open", identifier))
        return Runtime()

    monkeypatch.setattr(module, "open_installed_runtime", open_runtime)
    return seen


@pytest.mark.parametrize(
    "status", ["APPLIED", "REFUSED", "CONFLICT", "DEFERRED", "UNSETTLED"]
)
def test_execute_preserves_exact_status_and_closes_before_output(
    monkeypatch, capsys, status
):
    seen = fixture_runtime(monkeypatch, status=status)
    code = module.main(["execute", "fixture", ACCEPTANCE])
    assert code == (0 if status == "APPLIED" else 2)
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == status
    assert output["effect_key"] == "b" * 64
    assert seen == [("open", "fixture"), "enter", ("execute", ACCEPTANCE), "close"]


def test_list_metadata_uses_only_fixed_ledger_and_pagination_data(monkeypatch, capsys):
    seen = fixture_runtime(monkeypatch)
    assert (
        module.main(["list-accepted", "fixture", "--cursor", "YXNk", "--limit", "1"])
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["items"][0]["acceptance_id"] == ACCEPTANCE
    assert output["next_cursor"] is None
    assert seen[-2:] == [("list", "YXNk", 1), "close"]


def test_explicit_install_never_opens_executor_or_selects_path(monkeypatch, capsys):
    installed = []
    monkeypatch.setattr(
        module, "install_profile_journal", lambda value: installed.append(value)
    )
    monkeypatch.setattr(
        module, "open_installed_runtime", lambda *args: pytest.fail("runtime open")
    )
    assert module.main(["install-journal", "fixture"]) == 0
    assert installed == ["fixture"]
    assert json.loads(capsys.readouterr().out)["status"] == "INSTALLED"


@pytest.mark.parametrize(
    "flag",
    [
        "--factory",
        "--intent-source",
        "--credential",
        "--token",
        "--journal",
        "--path",
        "--environment",
    ],
)
def test_caller_overrides_rejected_before_any_resource(monkeypatch, capsys, flag):
    monkeypatch.setattr(
        module, "open_installed_runtime", lambda *args: pytest.fail("runtime open")
    )
    with pytest.raises(SystemExit) as caught:
        module.main(["execute", "fixture", ACCEPTANCE, flag, "fixture-selector"])
    assert caught.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["execute", "../fixture", ACCEPTANCE],
        ["execute", "fixture", "bad-id"],
        ["list-accepted", "fixture", "--limit", "65"],
        ["list-accepted", "fixture", "--cursor", "module:factory"],
    ],
)
def test_bad_scoped_data_refuses_before_private_read(monkeypatch, capsys, argv):
    monkeypatch.setattr(
        module, "open_installed_runtime", lambda *args: pytest.fail("runtime open")
    )
    with pytest.raises(SystemExit) as caught:
        module.main(argv)
    assert caught.value.code == 2
    assert capsys.readouterr().out == ""


def test_close_error_after_applied_does_not_claim_refused_or_success(
    monkeypatch, capsys
):
    seen = fixture_runtime(monkeypatch, close_failure=True)
    assert module.main(["execute", "fixture", ACCEPTANCE]) == 2
    raw = capsys.readouterr().out
    assert "sensitive" not in raw and "APPLIED" not in raw and "REFUSED" not in raw
    assert json.loads(raw)["status"] == "UNAVAILABLE"
    assert seen[-1] == "close"


def test_unavailable_diagnostic_never_prints_upstream_exception(monkeypatch, capsys):
    def failed(*args):
        raise OSError("fixture private key path and opaque token must never print")

    monkeypatch.setattr(module, "open_installed_runtime", failed)
    assert module.main(["execute", "fixture", ACCEPTANCE]) == 2
    raw = capsys.readouterr().out
    assert "opaque" not in raw and "private" not in raw
    assert json.loads(raw)["reason"] == "protected-runtime-unavailable"


def test_terminal_controls_and_bidi_are_escaped_on_output(capsys):
    module._emit({"fixture_data": "\x1b\r\u202e"})
    raw = capsys.readouterr().out
    assert "\x1b" not in raw and "\r" not in raw and "\u202e" not in raw
    assert json.loads(raw) == {"fixture_data": "\x1b\r\u202e"}
