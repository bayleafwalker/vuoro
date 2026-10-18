"""Real local durability/concurrency falsifiers; journal records grant no authority."""

import asyncio
import base64
from dataclasses import FrozenInstanceError
import fcntl
import json
import os
import select
import shutil
import sqlite3
import subprocess
import sys

import pytest

from vuoro_reconciler import execution_journal as module
from vuoro_reconciler.execution_journal import (
    ExecutionJournal,
    JournalRefused,
    install_journal,
)
from test_protected_copy_codec import FIXTURE, canonical, decode, sha

REGISTRY = canonical({"schema": "test-registry/v1", "profile": "test"})
HOST = "test-host"
FINGERPRINT = "f" * 40
PUBLIC_KEY_HASH = "sha256:" + "1" * 64
PROVIDER_HASH = "sha256:" + "2" * 64
SIGNING_HASH = "sha256:" + "3" * 64


def installed(tmp_path):
    path = tmp_path / "journal"
    install_journal(path, host_id=HOST, registry_utf8=REGISTRY)
    return path


def installed_v1(tmp_path):
    path = tmp_path / "journal-v1"
    path.mkdir(mode=0o700)
    (path / module.LOCK_NAME).touch(mode=0o600)
    database = path / module.DB_NAME
    database.touch(mode=0o600)
    with sqlite3.connect(database) as connection:
        for sql in (*module._V1_TABLE_SQL.values(), *module._V1_TRIGGER_SQL.values()):
            connection.execute(sql)
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO journal_metadata VALUES (1,1,?,?,?)",
            (HOST, REGISTRY, sha(REGISTRY)),
        )
    return path


def test_empty_v1_journal_rebind_is_atomic_and_audits_exact_registry_bytes(tmp_path):
    path = installed_v1(tmp_path)
    updated = canonical({"schema": "test-registry/v1", "profile": "updated"})
    receipt = module.rebind_empty_journal(
        path,
        host_id=HOST,
        profile_id="fixture-profile",
        expected_old_registry_sha256=sha(REGISTRY),
        new_registry_utf8=updated,
        operation_id="a" * 32,
    )
    assert receipt == {
        "operation_id": "a" * 32,
        "old_registry_sha256": sha(REGISTRY),
        "new_registry_sha256": sha(updated),
    }
    with sqlite3.connect(path / module.DB_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute(
            "SELECT host_id,registry_utf8,registry_sha256 FROM journal_metadata"
        ).fetchone() == (HOST, updated, sha(updated))
        assert connection.execute(
            "SELECT host_id,profile_id,old_registry_utf8,old_registry_sha256,new_registry_utf8,new_registry_sha256 FROM registry_rebinds"
        ).fetchone() == (HOST, "fixture-profile", REGISTRY, sha(REGISTRY), updated, sha(updated))
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    opened = ExecutionJournal(path, host_id=HOST, registry_utf8=updated)
    with opened.cycle():
        assert opened.registry_rebind_receipt("a" * 32) == {
            "operation_id": "a" * 32,
            "host_id": HOST,
            "profile_id": "fixture-profile",
            "old_registry_sha256": sha(REGISTRY),
            "new_registry_sha256": sha(updated),
        }
    opened.close()


def test_rebind_refuses_journal_replaced_between_factory_pin_and_lowlevel_open(tmp_path):
    path = installed_v1(tmp_path)
    pinned = tuple(
        (os.stat(path / name).st_dev, os.stat(path / name).st_ino)
        for name in (".", module.LOCK_NAME, module.DB_NAME)
    )
    backup = path / "journal.lock.old"
    os.replace(path / module.LOCK_NAME, backup)
    replacement = path / module.LOCK_NAME
    replacement.touch(mode=0o600)
    replacement.chmod(0o600)

    with pytest.raises(JournalRefused):
        module.rebind_empty_journal(
            path,
            host_id=HOST,
            profile_id="fixture-profile",
            expected_old_registry_sha256=sha(REGISTRY),
            new_registry_utf8=canonical({"schema": "test-registry/v1", "profile": "updated"}),
            operation_id="b" * 32,
            expected_physical_identities=pinned,
        )
    with sqlite3.connect(path / module.DB_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)


def test_rebind_identity_handoff_failure_before_commit_rolls_back_v1(tmp_path):
    path = installed_v1(tmp_path)
    calls = 0

    def identity_check():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise JournalRefused("fixture handoff drift")

    with pytest.raises(JournalRefused):
        module.rebind_empty_journal(
            path,
            host_id=HOST,
            profile_id="fixture-profile",
            expected_old_registry_sha256=sha(REGISTRY),
            new_registry_utf8=canonical({"schema": "test-registry/v1", "profile": "updated"}),
            operation_id="c" * 32,
            identity_check=identity_check,
        )
    assert calls == 2
    with sqlite3.connect(path / module.DB_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT registry_utf8,registry_sha256 FROM journal_metadata"
        ).fetchone() == (REGISTRY, sha(REGISTRY))


def test_rebind_post_fsync_identity_failure_is_receiptable_without_retry(tmp_path):
    path = installed_v1(tmp_path)
    updated = canonical({"schema": "test-registry/v1", "profile": "updated"})
    calls = 0

    def identity_check():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise JournalRefused("fixture post-fsync drift")

    with pytest.raises(JournalRefused):
        module.rebind_empty_journal(
            path,
            host_id=HOST,
            profile_id="fixture-profile",
            expected_old_registry_sha256=sha(REGISTRY),
            new_registry_utf8=updated,
            operation_id="d" * 32,
            identity_check=identity_check,
        )
    assert calls == 3
    journal = ExecutionJournal(path, host_id=HOST, registry_utf8=updated)
    try:
        with journal.cycle():
            assert journal.registry_rebind_receipt("d" * 32)["new_registry_sha256"] == sha(updated)
    finally:
        journal.close()


def test_empty_journal_can_rebind_twice_without_losing_transition_history(tmp_path):
    path = installed_v1(tmp_path)
    first = canonical({"schema": "test-registry/v1", "profile": "first"})
    second = canonical({"schema": "test-registry/v1", "profile": "second"})
    module.rebind_empty_journal(
        path,
        host_id=HOST,
        profile_id="fixture-profile",
        expected_old_registry_sha256=sha(REGISTRY),
        new_registry_utf8=first,
        operation_id="e" * 32,
    )
    module.rebind_empty_journal(
        path,
        host_id=HOST,
        profile_id="fixture-profile",
        expected_old_registry_sha256=sha(first),
        new_registry_utf8=second,
        operation_id="f" * 32,
    )
    with sqlite3.connect(path / module.DB_NAME) as connection:
        rows = connection.execute(
            "SELECT operation_id,old_registry_utf8,new_registry_utf8 FROM registry_rebinds ORDER BY rowid"
        ).fetchall()
        assert rows == [("e" * 32, REGISTRY, first), ("f" * 32, first, second)]
    opened = ExecutionJournal(path, host_id=HOST, registry_utf8=second)
    opened.close()


def test_existing_v1_journal_remains_operational_without_implicit_migration(tmp_path):
    path = installed_v1(tmp_path)
    journal = ExecutionJournal(path, host_id=HOST, registry_utf8=REGISTRY)
    try:
        with journal.cycle():
            assert journal.read("a" * 64) is None
            bind(journal)
    finally:
        journal.close()
    with sqlite3.connect(path / module.DB_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='registry_rebinds'"
        ).fetchone() is None


@pytest.mark.parametrize("failure", ["stale-hash", "foreign-host", "nonempty"])
def test_empty_v1_rebind_refuses_without_changing_legacy_journal(tmp_path, failure):
    path = installed_v1(tmp_path)
    if failure == "nonempty":
        with sqlite3.connect(path / module.DB_NAME) as connection:
            connection.execute(
                "INSERT INTO effect_bindings VALUES (?,?,?,?,?)",
                ("a" * 64, b"{}", sha(b"{}"), b"{}", sha(b"{}")),
            )
    with pytest.raises(JournalRefused):
        module.rebind_empty_journal(
            path,
            host_id="foreign" if failure == "foreign-host" else HOST,
            profile_id="fixture-profile",
            expected_old_registry_sha256="sha256:" + "0" * 64 if failure == "stale-hash" else sha(REGISTRY),
            new_registry_utf8=canonical({"schema": "test-registry/v1", "profile": "updated"}),
            operation_id="b" * 32,
        )
    with sqlite3.connect(path / module.DB_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("SELECT registry_utf8 FROM journal_metadata").fetchone() == (REGISTRY,)


def test_empty_v1_rebind_schema_failure_rolls_back_to_exact_v1_catalog(tmp_path, monkeypatch):
    path = installed_v1(tmp_path)
    original = (path / module.DB_NAME).read_bytes()
    catalog = module._catalog

    def fail_after_upgrade(connection):
        if connection.execute("PRAGMA user_version").fetchone()[0] == 2:
            raise ValueError("fixture failure")
        return catalog(connection)

    monkeypatch.setattr(module, "_catalog", fail_after_upgrade)
    with pytest.raises(JournalRefused):
        module.rebind_empty_journal(
            path,
            host_id=HOST,
            profile_id="fixture-profile",
            expected_old_registry_sha256=sha(REGISTRY),
            new_registry_utf8=canonical({"schema": "test-registry/v1", "profile": "updated"}),
            operation_id="c" * 32,
        )
    with sqlite3.connect(path / module.DB_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("SELECT registry_utf8 FROM journal_metadata").fetchone() == (REGISTRY,)
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='registry_rebinds'").fetchone() is None


def test_empty_v1_rebind_refuses_while_existing_journal_flock_is_held(tmp_path):
    path = installed_v1(tmp_path)
    lock_fd = os.open(path / module.LOCK_NAME, os.O_RDWR)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(JournalRefused):
            module.rebind_empty_journal(
                path,
                host_id=HOST,
                profile_id="fixture-profile",
                expected_old_registry_sha256=sha(REGISTRY),
                new_registry_utf8=canonical({"schema": "test-registry/v1", "profile": "updated"}),
                operation_id="9" * 32,
            )
        with sqlite3.connect(path / module.DB_NAME) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (1,)
            assert connection.execute("SELECT registry_utf8 FROM journal_metadata").fetchone() == (REGISTRY,)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


@pytest.mark.parametrize("phase", ["before-commit", "after-commit"])
@pytest.mark.parametrize("target", [module.DB_NAME, module.LOCK_NAME])
def test_empty_v1_rebind_detects_named_database_or_lock_replacement(
    tmp_path, monkeypatch, phase, target
):
    path = installed_v1(tmp_path)
    backup = path / (target + ".original")
    if phase == "before-commit":
        check = module._rebind_files_check
        calls = 0

        def replace_before_commit(directory, directory_fd, lock_fd, database_fd, identities):
            nonlocal calls
            calls += 1
            if calls == 2:
                os.replace(path / target, backup)
                replacement = path / target
                replacement.touch(mode=0o600)
                replacement.chmod(0o600)
            return check(directory, directory_fd, lock_fd, database_fd, identities)

        monkeypatch.setattr(module, "_rebind_files_check", replace_before_commit)
    else:
        fsync = module._fsync_files

        def replace_after_commit(directory_fd, database_fd):
            fsync(directory_fd, database_fd)
            os.replace(path / target, backup)
            replacement = path / target
            replacement.touch(mode=0o600)
            replacement.chmod(0o600)

        monkeypatch.setattr(module, "_fsync_files", replace_after_commit)
    with pytest.raises(JournalRefused):
        module.rebind_empty_journal(
            path,
            host_id=HOST,
            profile_id="fixture-profile",
            expected_old_registry_sha256=sha(REGISTRY),
            new_registry_utf8=canonical({"schema": "test-registry/v1", "profile": "updated"}),
            operation_id="8" * 32,
        )
    assert backup.exists()
    if phase == "before-commit":
        with sqlite3.connect(backup if target == module.DB_NAME else path / module.DB_NAME) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    elif target == module.DB_NAME:
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (2,)
            assert connection.execute("SELECT operation_id FROM registry_rebinds").fetchone() == ("8" * 32,)


@pytest.fixture
def journal(tmp_path):
    path = installed(tmp_path)
    instance = ExecutionJournal(path, host_id=HOST, registry_utf8=REGISTRY)
    yield instance
    instance.close()


def bind(journal, **overrides):
    effect = decode()
    args = dict(
        protected_copy_utf8=FIXTURE.read_bytes(),
        provider_profile_sha256=PROVIDER_HASH,
        signing_profile_sha256=SIGNING_HASH,
        publication_target_utf8=canonical(
            {
                "schema": "vuoro-protected-publication-target/v1",
                "repository": effect.repository,
                "head_repository": effect.repository,
                "base_branch": "main",
            }
        ),
        expected_signer_fingerprint=FINGERPRINT,
        expected_public_key_sha256=PUBLIC_KEY_HASH,
    )
    args.update(overrides)
    return journal.bind(effect, **args)


def local(record):
    params = json.loads(decode().parameters_utf8)
    message = b"Approved candidate message\n"
    return {
        "schema": "vuoro-protected-local-ready/v1",
        "commit_oid": "a" * 40,
        "tree_oid": "c" * 40,
        "base_parent_oid": params["base_commit"],
        "message_utf8_b64": base64.b64encode(message).decode(),
        "message_sha256": sha(message),
        "signer_fingerprint": FINGERPRINT,
        "public_key_sha256": PUBLIC_KEY_HASH,
        "artifact_relative_path": "artifacts/original.bundle",
        "artifact_sha256": "sha256:" + "4" * 64,
    }


def prepared(journal):
    record = bind(journal)
    journal.record_local_ready(record.effect_key, canonical(local(record)))
    return journal.read(record.effect_key)


def branch_request(record):
    return {
        "schema": "vuoro-protected-branch-request/v1",
        "effect_key": record.effect_key,
        "binding_sha256": sha(record.binding_utf8),
        "repository": decode().repository,
        "branch": "vuoro-protected/" + record.effect_key,
        "commit_oid": "a" * 40,
        "base_parent_oid": json.loads(decode().parameters_utf8)["base_commit"],
        "provider_profile_sha256": PROVIDER_HASH,
    }


def branch_observed(record, request):
    return {
        "schema": "vuoro-protected-branch-observation/v1",
        **{
            k: request[k]
            for k in (
                "effect_key",
                "binding_sha256",
                "repository",
                "branch",
                "commit_oid",
            )
        },
        "request_sha256": sha(canonical(request)),
    }


def pr_request(record):
    effect = decode()
    binding = json.loads(record.binding_utf8)
    params = json.loads(effect.parameters_utf8)
    public = {
        "schema": "vuoro-protected-publication-binding/v1",
        "effect_key": record.effect_key,
        "binding_sha256": sha(record.binding_utf8),
        "locator_utf8_b64": base64.b64encode(effect.locator_utf8).decode(),
        "canonical_hash": binding["canonical_hash"],
        "legacy_content_digest": binding["legacy_content_digest"],
        "acceptance_id": binding["acceptance_id"],
        "acceptance_revision": binding["acceptance_revision"],
        "receipt_id": binding["receipt_id"],
        "source_run_utf8_b64": base64.b64encode(effect.source_run.encode()).decode(),
        "commit_oid": "a" * 40,
    }
    body = (
        params["rationale"]
        + "\n\n<!-- vuoro-protected-publication/v1 "
        + base64.b64encode(canonical(public)).decode()
        + " -->\n"
    ).encode()
    return {
        "schema": "vuoro-protected-pr-request/v1",
        "effect_key": record.effect_key,
        "binding_sha256": sha(record.binding_utf8),
        "repository": effect.repository,
        "head_repository": effect.repository,
        "branch": "vuoro-protected/" + record.effect_key,
        "commit_oid": "a" * 40,
        "base_branch": "main",
        "title": params["title"],
        "body_utf8_b64": base64.b64encode(body).decode(),
        "body_sha256": sha(body),
        "provider_profile_sha256": PROVIDER_HASH,
    }


def pr_observed(request, state="open"):
    return {
        "schema": "vuoro-protected-pr-observation/v1",
        **{
            k: v
            for k, v in request.items()
            if k not in ("schema", "provider_profile_sha256")
        },
        "request_sha256": sha(canonical(request)),
        "pr_number": 23,
        "pr_url": "https://forge.example/repo/pulls/23",
        "state": state,
    }


def outcome(record, branch, pr):
    return {
        "schema": "vuoro-protected-applied-outcome/v1",
        "effect_key": record.effect_key,
        "binding_sha256": sha(record.binding_utf8),
        "commit_oid": "a" * 40,
        "branch_request_sha256": sha(canonical(branch)),
        "pr_request_sha256": sha(canonical(pr)),
        "pr_number": 23,
        "pr_url": "https://forge.example/repo/pulls/23",
    }


def publish_data(journal, state="open"):
    record = prepared(journal)
    branch = branch_request(record)
    assert journal.mark_spent(record.effect_key, "branch-create", canonical(branch))
    journal.record_observed(
        record.effect_key, "BRANCH_OBSERVED", canonical(branch_observed(record, branch))
    )
    pr = pr_request(record)
    assert journal.mark_spent(record.effect_key, "pr-create", canonical(pr))
    journal.record_observed(
        record.effect_key, "PR_OBSERVED", canonical(pr_observed(pr, state))
    )
    return record, branch, pr


def test_exclusive_install_runtime_no_ddl_and_immutable_records(journal):
    with pytest.raises(JournalRefused):
        install_journal(journal.directory, host_id=HOST, registry_utf8=REGISTRY)
    with journal.cycle():
        traced = []
        journal._connection.set_trace_callback(traced.append)
        record = bind(journal)
        assert record == bind(journal)
        assert journal.read(record.effect_key) == record
        assert record.status == "BOUND" and "test-host" not in repr(record)
        assert not any(
            s.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for s in traced
        )
        with pytest.raises(FrozenInstanceError):
            record.effect_key = "f" * 64
    with pytest.raises(JournalRefused):
        journal.read(record.effect_key)


@pytest.mark.parametrize(
    "change",
    ["host", "registry", "version", "extra-table", "drop-trigger", "metadata-hash"],
)
def test_installed_metadata_and_exact_catalog_drift_refused(tmp_path, change):
    path = installed(tmp_path)
    if change not in ("host", "registry"):
        with sqlite3.connect(path / module.DB_NAME) as connection:
            if change == "version":
                connection.execute("PRAGMA user_version=3")
            elif change == "extra-table":
                connection.execute("CREATE TABLE extra (x TEXT)")
            elif change == "drop-trigger":
                connection.execute("DROP TRIGGER effect_bindings_no_delete")
            else:
                connection.execute("DROP TRIGGER journal_metadata_no_update")
                connection.execute(
                    "UPDATE journal_metadata SET registry_sha256=?",
                    ("sha256:" + "0" * 64,),
                )
                connection.execute(module._TRIGGER_SQL["journal_metadata_no_update"])
    with pytest.raises(JournalRefused):
        ExecutionJournal(
            path,
            host_id="foreign" if change == "host" else HOST,
            registry_utf8=b"{}" if change == "registry" else REGISTRY,
        )


@pytest.mark.parametrize("table", [name for name in module._TABLE_SQL if name != "registry_rebinds"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_every_table_has_actual_immutable_guard(journal, table, verb):
    with journal.cycle():
        record, branch, pr = publish_data(journal)
        journal.record_outcome(
            record.effect_key, canonical(outcome(record, branch, pr))
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable-journal"):
            if verb == "DELETE":
                journal._connection.execute(f"DELETE FROM {table}")
            else:
                field = "host_id" if table == "journal_metadata" else "effect_key"
                journal._connection.execute(f"UPDATE {table} SET {field}={field}")


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_registry_rebind_receipt_has_actual_immutable_guard(tmp_path, verb):
    path = installed_v1(tmp_path)
    updated = canonical({"schema": "test-registry/v1", "profile": "updated"})
    module.rebind_empty_journal(
        path,
        host_id=HOST,
        profile_id="fixture-profile",
        expected_old_registry_sha256=sha(REGISTRY),
        new_registry_utf8=updated,
        operation_id="d" * 32,
    )
    with sqlite3.connect(path / module.DB_NAME) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable-journal"):
            connection.execute(
                f"{verb} registry_rebinds SET operation_id=operation_id WHERE operation_id=?"
                if verb == "UPDATE"
                else "DELETE FROM registry_rebinds WHERE operation_id=?",
                ("d" * 32,),
            )


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("provider_profile_sha256", "sha256:" + "9" * 64),
        ("signing_profile_sha256", "sha256:" + "9" * 64),
        ("expected_signer_fingerprint", "e" * 40),
        ("expected_public_key_sha256", "sha256:" + "9" * 64),
    ],
)
def test_different_binding_conflicts_without_poisoning_original(
    journal, field, replacement
):
    with journal.cycle():
        original = bind(journal)
        with pytest.raises(JournalRefused):
            bind(journal, **{field: replacement})
        assert bind(journal) == original


@pytest.mark.parametrize(
    "bad",
    [
        "HEAD",
        "feature/../bad",
        "../main",
        "-main",
        "main.lock",
        "a@{b",
        "a\\b",
        "a b",
        "/main",
        "main.",
        "a//b",
        "",
        "main\x00",
    ],
)
def test_publication_target_branch_lexical_refusal(journal, bad):
    with journal.cycle():
        with pytest.raises(JournalRefused):
            bind(
                journal,
                publication_target_utf8=canonical(
                    {
                        "schema": "vuoro-protected-publication-target/v1",
                        "repository": decode().repository,
                        "head_repository": decode().repository,
                        "base_branch": bad,
                    }
                ),
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_oid", "e" * 64),
        ("tree_oid", "e" * 64),
        ("base_parent_oid", "0" * 40),
        ("signer_fingerprint", "f" * 16),
        ("signer_fingerprint", "e" * 40),
        ("public_key_sha256", "sha256:" + "0" * 64),
        ("message_sha256", "sha256:" + "0" * 64),
        ("artifact_relative_path", "../object"),
        ("artifact_relative_path", "/object"),
        ("artifact_relative_path", "a//b"),
        ("artifact_relative_path", "a/./b"),
        ("artifact_relative_path", "élément"),
        ("artifact_relative_path", "a\x00b"),
    ],
)
def test_local_ready_original_parent_signer_shape_and_artifact_closure(
    journal, field, value
):
    with journal.cycle():
        record = bind(journal)
        data = local(record)
        data[field] = value
        with pytest.raises(JournalRefused):
            journal.record_local_ready(record.effect_key, canonical(data))
        assert journal.read(record.effect_key).status == "BOUND"


def test_original_oid_cannot_be_replaced_and_spent_never_reset(journal):
    with journal.cycle():
        record = prepared(journal)
        alternative = local(record)
        alternative["commit_oid"] = "e" * 40
        with pytest.raises(JournalRefused):
            journal.record_local_ready(record.effect_key, canonical(alternative))
        request = branch_request(record)
        assert (
            journal.mark_spent(record.effect_key, "branch-create", canonical(request))
            is True
        )
        assert (
            journal.mark_spent(record.effect_key, "branch-create", canonical(request))
            is False
        )
        assert journal.read(record.effect_key).status == "UNSETTLED"
        request["commit_oid"] = "e" * 40
        with pytest.raises(JournalRefused):
            journal.mark_spent(record.effect_key, "branch-create", canonical(request))


@pytest.mark.parametrize(
    "field,value",
    [
        ("effect_key", "0" * 64),
        ("binding_sha256", "sha256:" + "0" * 64),
        ("repository", "foreign"),
        ("branch", "main"),
        ("commit_oid", "e" * 40),
        ("base_parent_oid", "e" * 40),
        ("provider_profile_sha256", "sha256:" + "0" * 64),
    ],
)
def test_branch_request_one_field_off_refuses_before_spent(journal, field, value):
    with journal.cycle():
        record = prepared(journal)
        request = branch_request(record)
        request[field] = value
        with pytest.raises(JournalRefused):
            journal.mark_spent(record.effect_key, "branch-create", canonical(request))
        assert not journal.read(record.effect_key).spent


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_repository", "fork"),
        ("base_branch", "other"),
        ("title", "other"),
        ("commit_oid", "e" * 40),
        ("provider_profile_sha256", "sha256:" + "0" * 64),
        ("body", "arbitrary body"),
        ("footer", "different original receipt"),
    ],
)
def test_pr_request_frozen_title_rationale_footer_and_target_joins(
    journal, field, value
):
    with journal.cycle():
        record = prepared(journal)
        branch = branch_request(record)
        journal.mark_spent(record.effect_key, "branch-create", canonical(branch))
        journal.record_observed(
            record.effect_key,
            "BRANCH_OBSERVED",
            canonical(branch_observed(record, branch)),
        )
        request = pr_request(record)
        if field in ("body", "footer"):
            body = (
                value.encode()
                if field == "body"
                else base64.b64decode(request["body_utf8_b64"]) + value.encode()
            )
            request["body_utf8_b64"] = base64.b64encode(body).decode()
            request["body_sha256"] = sha(body)
        else:
            request[field] = value
        with pytest.raises(JournalRefused):
            journal.mark_spent(record.effect_key, "pr-create", canonical(request))
        assert len(journal.read(record.effect_key).spent) == 1


def test_observations_require_markers_and_exact_requests(journal):
    with journal.cycle():
        record = prepared(journal)
        branch = branch_request(record)
        observation = branch_observed(record, branch)
        with pytest.raises(JournalRefused):
            journal.record_observed(
                record.effect_key, "BRANCH_OBSERVED", canonical(observation)
            )
        with pytest.raises(JournalRefused):
            journal.mark_spent(
                record.effect_key, "pr-create", canonical(pr_request(record))
            )
        journal.mark_spent(record.effect_key, "branch-create", canonical(branch))
        for field, value in (
            ("request_sha256", "sha256:" + "0" * 64),
            ("commit_oid", "e" * 40),
        ):
            with pytest.raises(JournalRefused):
                journal.record_observed(
                    record.effect_key,
                    "BRANCH_OBSERVED",
                    canonical({**observation, field: value}),
                )


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_repository", "fork"),
        ("base_branch", "other"),
        ("commit_oid", "e" * 40),
        ("request_sha256", "sha256:" + "0" * 64),
        ("pr_number", True),
        ("state", "unknown"),
        ("pr_url", "https://user:secret@forge.example/x"),
        ("pr_url", "http://forge.example/x"),
        ("pr_url", "https://forge.example/x?token=x"),
    ],
)
def test_pr_observation_exact_joins_and_types(journal, field, value):
    with journal.cycle():
        record = prepared(journal)
        branch = branch_request(record)
        journal.mark_spent(record.effect_key, "branch-create", canonical(branch))
        journal.record_observed(
            record.effect_key,
            "BRANCH_OBSERVED",
            canonical(branch_observed(record, branch)),
        )
        pr = pr_request(record)
        journal.mark_spent(record.effect_key, "pr-create", canonical(pr))
        with pytest.raises(JournalRefused):
            journal.record_observed(
                record.effect_key,
                "PR_OBSERVED",
                canonical({**pr_observed(pr), field: value}),
            )


@pytest.mark.parametrize("state", ["open", "merged"])
def test_original_outcome_immutable_and_repeat_is_data(journal, state):
    with journal.cycle():
        record, branch, pr = publish_data(journal, state)
        result = canonical(outcome(record, branch, pr))
        original = journal.record_outcome(record.effect_key, result)
        assert original.status == "APPLIED"
        assert journal.record_outcome(record.effect_key, result) == original
        with pytest.raises(JournalRefused):
            journal.record_outcome(
                record.effect_key,
                canonical({**json.loads(result), "commit_oid": "e" * 40}),
            )
        assert journal.read(record.effect_key) == original
        with pytest.raises(JournalRefused):
            bind(journal, provider_profile_sha256="sha256:" + "9" * 64)
        assert journal.read(record.effect_key) == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_oid", "e" * 40),
        ("branch_request_sha256", "sha256:" + "0" * 64),
        ("pr_request_sha256", "sha256:" + "0" * 64),
        ("pr_number", True),
        ("pr_number", 24),
        ("pr_url", "https://forge.example/other/pulls/23"),
    ],
)
def test_outcome_original_tuple_one_field_off(journal, field, value):
    with journal.cycle():
        record, branch, pr = publish_data(journal)
        data = outcome(record, branch, pr)
        data[field] = value
        with pytest.raises(JournalRefused):
            journal.record_outcome(record.effect_key, canonical(data))
        assert journal.read(record.effect_key).outcome_utf8 is None


def test_closed_pr_permanent_refusal_without_public_currentness_poison(journal):
    with journal.cycle():
        record, branch, pr = publish_data(journal, "closed")
        with pytest.raises(JournalRefused):
            journal.record_outcome(
                record.effect_key, canonical(outcome(record, branch, pr))
            )
        terminal = {
            "schema": "vuoro-protected-journal-terminal/v1",
            "effect_key": record.effect_key,
            "binding_sha256": sha(record.binding_utf8),
            "reason": "public-basis-changed",
            "operation": "pr-create",
            "request_sha256": sha(canonical(pr)),
        }
        with pytest.raises(JournalRefused):
            journal.record_observed(record.effect_key, "REFUSED", canonical(terminal))
        terminal["reason"] = "original-pr-closed"
        assert (
            journal.record_observed(
                record.effect_key, "REFUSED", canonical(terminal)
            ).status
            == "REFUSED"
        )


@pytest.mark.parametrize(
    "kind",
    ["missing-marker", "missing-event", "extra-event", "copy-hash", "wrong-origin-oid"],
)
def test_read_replays_rows_and_closed_events_even_after_local_hash_recomputation(
    journal, kind
):
    with journal.cycle():
        record = prepared(journal)
        branch = branch_request(record)
        journal.mark_spent(record.effect_key, "branch-create", canonical(branch))
    with sqlite3.connect(journal.directory / module.DB_NAME) as connection:
        if kind == "missing-marker":
            connection.execute("DROP TRIGGER publication_spent_no_delete")
            connection.execute("DELETE FROM publication_spent")
            connection.execute(module._TRIGGER_SQL["publication_spent_no_delete"])
        elif kind == "missing-event":
            connection.execute("DROP TRIGGER journal_events_no_delete")
            connection.execute("DELETE FROM journal_events WHERE seq=2")
            connection.execute(module._TRIGGER_SQL["journal_events_no_delete"])
        elif kind == "extra-event":
            connection.execute(
                "INSERT INTO journal_events VALUES (?,?,?,?,?)",
                (record.effect_key, 3, "OPAQUE", b"{}", sha(b"{}")),
            )
        elif kind == "copy-hash":
            connection.execute("DROP TRIGGER effect_bindings_no_update")
            connection.execute(
                "UPDATE effect_bindings SET protected_copy_sha256=?",
                ("sha256:" + "0" * 64,),
            )
            connection.execute(module._TRIGGER_SQL["effect_bindings_no_update"])
        else:
            data = branch_request(record)
            data["commit_oid"] = "e" * 40
            raw = canonical(data)
            for table, field in (
                ("publication_spent", "request"),
                ("journal_events", "payload"),
            ):
                connection.execute(f"DROP TRIGGER {table}_no_update")
                clause = "" if table == "publication_spent" else " WHERE seq=2"
                connection.execute(
                    f"UPDATE {table} SET {field}_utf8=?,{field}_sha256=?" + clause,
                    (raw, sha(raw)),
                )
                connection.execute(module._TRIGGER_SQL[f"{table}_no_update"])
    with journal.cycle():
        with pytest.raises(JournalRefused):
            journal.read(record.effect_key)


@pytest.mark.parametrize(
    "kind", ["db", "lock", "directory", "mode", "symlink", "hardlink"]
)
def test_actual_filesystem_replacement_and_permissions_refuse(journal, kind, tmp_path):
    with journal.cycle():
        record = bind(journal)
    path = journal.directory
    if kind in ("db", "lock"):
        target = path / (module.DB_NAME if kind == "db" else module.LOCK_NAME)
        old = path / "saved"
        target.rename(old)
        shutil.copyfile(old, target)
        target.chmod(0o600)
    elif kind == "directory":
        path.rename(tmp_path / "moved")
        path.mkdir(mode=0o700)
    elif kind == "mode":
        (path / module.DB_NAME).chmod(0o644)
    elif kind == "symlink":
        target = path / module.LOCK_NAME
        target.rename(path / "saved-lock")
        target.symlink_to(path / "saved-lock")
    else:
        os.link(path / module.DB_NAME, tmp_path / "linked-db")
    with pytest.raises(JournalRefused):
        with journal.cycle():
            journal.read(record.effect_key)


@pytest.mark.parametrize("stage", ["commit", "database-fsync", "directory-fsync"])
def test_durability_failure_signals_no_publication_and_never_reopens_signal(
    journal, monkeypatch, stage
):
    with journal.cycle():
        record = prepared(journal)
        request = canonical(branch_request(record))
        sends = []
        real_fsync = module.os.fsync
        real_commit = journal._commit

        def fail_fsync(fd):
            wanted = (
                journal._database_fd
                if stage == "database-fsync"
                else journal._directory_fd
            )
            if fd == wanted:
                raise OSError("synthetic failure with secret-looking detail")
            real_fsync(fd)

        def fail_commit():
            raise sqlite3.OperationalError("synthetic uncertainty")

        if stage == "commit":
            monkeypatch.setattr(journal, "_commit", fail_commit)
        else:
            monkeypatch.setattr(module.os, "fsync", fail_fsync)
        with pytest.raises(
            JournalRefused, match="^execution-journal-invalid$"
        ) as error:
            if journal.mark_spent(record.effect_key, "branch-create", request):
                sends.append("publication")
        assert (
            not sends
            and error.value.__cause__ is None
            and error.value.__context__ is None
        )
        monkeypatch.setattr(module.os, "fsync", real_fsync)
        monkeypatch.setattr(journal, "_commit", real_commit)
        with pytest.raises(JournalRefused):
            journal.mark_spent(record.effect_key, "branch-create", request)
    # A fresh process/object may see committed markers after fsync uncertainty;
    # it must return false. An actual precommit failure left no possible send.
    replacement = ExecutionJournal(
        journal.directory, host_id=HOST, registry_utf8=REGISTRY
    )
    try:
        with replacement.cycle():
            assert replacement.mark_spent(
                record.effect_key, "branch-create", request
            ) is (stage == "commit")
    finally:
        replacement.close()


def test_nested_and_wrong_async_caller_cycle_refused(journal):
    with journal.cycle():
        record = bind(journal)
        with pytest.raises(JournalRefused):
            with journal.cycle():
                pass
        assert journal.read(record.effect_key) == record

    async def exercise():
        with journal.cycle():

            async def other():
                with pytest.raises(JournalRefused):
                    journal.read(record.effect_key)
                with pytest.raises(JournalRefused):
                    with journal.cycle():
                        pass

            await asyncio.create_task(other())
            assert journal.read(record.effect_key) == record

    asyncio.run(exercise())


def child(journal, body):
    script = "\n".join(
        [
            "from pathlib import Path",
            "from vuoro_reconciler.execution_journal import ExecutionJournal",
            "import sys",
            f"path=Path({str(journal.directory)!r})",
            f"registry={REGISTRY!r}",
            "print('START',flush=True)",
            f"j=ExecutionJournal(path,host_id={HOST!r},registry_utf8=registry)",
            body,
        ]
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def line(process, seconds=10):
    assert select.select([process.stdout], [], [], seconds)[0], (
        "child did not reach expected boundary"
    )
    return process.stdout.readline().strip()


def test_actual_two_process_flock_serializes_full_cycle_and_kill_releases(journal):
    first = child(
        journal, "with j.cycle():\n print('LOCKED',flush=True)\n sys.stdin.readline()"
    )
    second = None
    try:
        assert line(first) == "START"
        assert line(first) == "LOCKED"
        second = child(
            journal, "with j.cycle():\n print('ACQUIRED',flush=True)\nj.close()"
        )
        assert line(second) == "START"
        assert not select.select([second.stdout], [], [], 0.2)[0]
        first.kill()
        first.wait(timeout=10)
        assert line(second) == "ACQUIRED"
        assert second.wait(timeout=10) == 0, second.stderr.read()
    finally:
        for process in (first, second):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)


def test_kill_after_marker_before_send_never_retransmits_absent_publication(journal):
    with journal.cycle():
        record = prepared(journal)
        request = canonical(branch_request(record))
    marker_path = journal.directory / "request.json"
    marker_path.write_bytes(request)
    sentinel = journal.directory / "sent"
    body = f"with j.cycle():\n assert j.mark_spent({record.effect_key!r},'branch-create',(path/'request.json').read_bytes())\n print('MARKED',flush=True)\n sys.stdin.readline()\n (path/'sent').write_text('sent')"
    process = child(journal, body)
    try:
        assert line(process) == "START"
        assert line(process) == "MARKED"
        process.kill()
        process.wait(timeout=10)
        assert not sentinel.exists()
        with journal.cycle():
            # Actual remote absence would be DATA only; there is no clearing API.
            assert journal.read(record.effect_key).status == "UNSETTLED"
            assert (
                journal.mark_spent(record.effect_key, "branch-create", request) is False
            )
        assert not sentinel.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":1.0}', b'{"x":NaN}', b'{"x":"\\ud800"}', b"[]"]
)
def test_no_opaque_payload_or_noncanonical_registry(journal, raw):
    with journal.cycle():
        record = bind(journal)
        with pytest.raises(JournalRefused):
            journal.record_local_ready(record.effect_key, raw)
    with pytest.raises(JournalRefused):
        ExecutionJournal(journal.directory, host_id=HOST, registry_utf8=raw)


@pytest.mark.parametrize("field", ["acceptance_revision", "intent_revision"])
def test_redigested_bool_binding_is_not_original_integer_tuple(journal, field):
    with journal.cycle():
        record = bind(journal)
    data = json.loads(record.binding_utf8)
    data[field] = True
    raw = canonical(data)
    with sqlite3.connect(journal.directory / module.DB_NAME) as connection:
        for table in ("effect_bindings", "journal_events"):
            connection.execute(f"DROP TRIGGER {table}_no_update")
        connection.execute(
            "UPDATE effect_bindings SET binding_utf8=?,binding_sha256=?",
            (raw, sha(raw)),
        )
        connection.execute(
            "UPDATE journal_events SET payload_utf8=?,payload_sha256=? WHERE seq=0",
            (raw, sha(raw)),
        )
        for table in ("effect_bindings", "journal_events"):
            connection.execute(module._TRIGGER_SQL[f"{table}_no_update"])
    with journal.cycle():
        with pytest.raises(JournalRefused):
            journal.read(record.effect_key)


def test_fork_inherited_instance_refuses_shared_open_lock_description(journal):
    read_fd, write_fd = os.pipe()
    with journal.cycle():
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                with journal.cycle():
                    os.write(write_fd, b"overlap")
            except JournalRefused:
                os.write(write_fd, b"refused")
            finally:
                os.close(write_fd)
                os._exit(0)
        os.close(write_fd)
        try:
            assert select.select([read_fd], [], [], 10)[0]
            assert os.read(read_fd, 32) == b"refused"
            _, status = os.waitpid(pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0
        finally:
            os.close(read_fd)
        assert bind(journal).status == "BOUND"


def test_connection_close_failure_releases_mutex_and_flock(journal):
    class FailedClose:
        def __init__(self, connection):
            self.connection = connection

        def close(self):
            self.connection.close()
            raise sqlite3.OperationalError("synthetic close failure")

    with pytest.raises(JournalRefused):
        with journal.cycle():
            journal._connection = FailedClose(journal._connection)
    assert journal._mutex_entry[1] is None
    assert not journal._mutex_entry[0].locked()
    replacement = ExecutionJournal(
        journal.directory, host_id=HOST, registry_utf8=REGISTRY
    )
    try:
        with replacement.cycle():
            assert bind(replacement).status == "BOUND"
    finally:
        replacement.close()


def test_receipt_and_target_differences_cannot_poison_original_tuple(journal):
    value = json.loads(FIXTURE.read_bytes())
    value["receipt"]["receipt_id"] = "iar_" + "e" * 32
    altered_raw = canonical(value)
    altered = module._decode_copy(altered_raw)
    with journal.cycle():
        original = bind(journal)
        with pytest.raises(JournalRefused):
            journal.bind(
                altered,
                protected_copy_utf8=altered_raw,
                provider_profile_sha256=PROVIDER_HASH,
                signing_profile_sha256=SIGNING_HASH,
                publication_target_utf8=canonical(
                    json.loads(original.binding_utf8)["publication_target"]
                ),
                expected_signer_fingerprint=FINGERPRINT,
                expected_public_key_sha256=PUBLIC_KEY_HASH,
            )
        target = json.loads(original.binding_utf8)["publication_target"]
        target["base_branch"] = "different-valid-branch"
        with pytest.raises(JournalRefused):
            bind(journal, publication_target_utf8=canonical(target))
        assert journal.read(original.effect_key) == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("effect_key", "0" * 64),
        ("binding_sha256", "sha256:" + "0" * 64),
        ("request_sha256", "sha256:" + "0" * 64),
        ("repository", "foreign"),
        ("branch", "other"),
        ("commit_oid", "e" * 40),
    ],
)
def test_branch_observed_all_original_request_joins(journal, field, value):
    with journal.cycle():
        record = prepared(journal)
        request = branch_request(record)
        journal.mark_spent(record.effect_key, "branch-create", canonical(request))
        data = branch_observed(record, request)
        data[field] = value
        with pytest.raises(JournalRefused):
            journal.record_observed(
                record.effect_key, "BRANCH_OBSERVED", canonical(data)
            )


def test_canonical_reference_set_hash_is_exact_original_domain(journal):
    with journal.cycle():
        record = bind(journal)
        effect = decode()
        expected = sha(canonical(json.loads(effect.canonical_utf8)["evidence_refs"]))
        assert (
            json.loads(record.binding_utf8)["frozen_reference_set_sha256"] == expected
        )


def test_between_transaction_inode_replacement_never_returns_spent_signal(
    journal, monkeypatch
):
    with journal.cycle():
        record = prepared(journal)
        request = canonical(branch_request(record))
        original = journal._commit

        def commit_then_replace():
            original()
            target = journal.directory / module.DB_NAME
            target.rename(journal.directory / "saved-db")
            shutil.copyfile(journal.directory / "saved-db", target)
            target.chmod(0o600)

        monkeypatch.setattr(journal, "_commit", commit_then_replace)
        sends = []
        with pytest.raises(JournalRefused):
            if journal.mark_spent(record.effect_key, "branch-create", request):
                sends.append("send")
        assert sends == []


def test_no_symlink_ancestry_and_missing_installation_are_runtime_refusals(tmp_path):
    with pytest.raises(JournalRefused):
        ExecutionJournal(tmp_path / "missing", host_id=HOST, registry_utf8=REGISTRY)
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(JournalRefused):
        install_journal(alias / "journal", host_id=HOST, registry_utf8=REGISTRY)
    assert not (real / "journal").exists()


def test_bind_caps_full_fingerprint_and_strict_registry_bytes(journal, tmp_path):
    with journal.cycle():
        with pytest.raises(JournalRefused):
            bind(journal, expected_signer_fingerprint="f" * 16)
        with pytest.raises(JournalRefused):
            bind(journal, expected_signer_fingerprint="F" * 40)
    noncanonical = json.dumps(json.loads(REGISTRY), indent=2).encode()
    with pytest.raises(JournalRefused):
        install_journal(tmp_path / "other", host_id=HOST, registry_utf8=noncanonical)
    assert not (tmp_path / "other").exists()


def test_install_parent_fsync_failure_refuses_and_runtime_never_repairs_incomplete(
    tmp_path, monkeypatch
):
    parent_identity = (os.stat(tmp_path).st_dev, os.stat(tmp_path).st_ino)
    original_fsync = module.os.fsync
    visited = []

    def fail_parent(fd):
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        visited.append(identity)
        if identity == parent_identity:
            raise OSError("synthetic parent directory failure")
        original_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_parent)
    with pytest.raises(JournalRefused):
        install_journal(tmp_path / "journal", host_id=HOST, registry_utf8=REGISTRY)
    assert parent_identity in visited
    monkeypatch.setattr(module.os, "fsync", original_fsync)
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir(mode=0o700)
    for name in (module.DB_NAME, module.LOCK_NAME):
        fd = os.open(incomplete / name, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
    with pytest.raises(JournalRefused):
        ExecutionJournal(incomplete, host_id=HOST, registry_utf8=REGISTRY)
    with sqlite3.connect(incomplete / module.DB_NAME) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_local_message_inside_frozen_16k_wire_budget_is_accepted(journal):
    with journal.cycle():
        record = bind(journal)
        data = local(record)
        message = b"x" * (9 * 1024)
        data["message_utf8_b64"] = base64.b64encode(message).decode()
        data["message_sha256"] = sha(message)
        raw = canonical(data)
        assert len(raw) < 16 * 1024
        assert (
            journal.record_local_ready(record.effect_key, raw).status == "LOCAL_READY"
        )
