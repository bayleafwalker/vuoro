"""Phase/fault oracles; all journal installation and effects are disposable."""

import asyncio
from dataclasses import dataclass
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from vuoro_reconciler import execution_journal as journal_module
from vuoro_reconciler import protected_executor as module
from vuoro_reconciler import protected_git as git_module
from vuoro_reconciler.protected_currentness import decode_current_basis
from vuoro_reconciler.protected_ledger_source import ProtectedCopyRead
from vuoro_reconciler.protected_executor import ProtectedExecutor
from test_protected_copy_codec import canonical, sha
from test_protected_currentness import basis as basis_document
import test_protected_git as git_fixtures

keys = git_fixtures.keys


@dataclass(frozen=True)
class Profile:
    signing: object
    profile_utf8: bytes
    registry_sha256: str
    profile_id: str = "disposable"

    @property
    def profile_sha256(self):
        return sha(self.profile_utf8)

    @property
    def data(self):
        return json.loads(self.profile_utf8)


class Ledger:
    def __init__(self, value):
        self.value = value
        self.profile = SimpleNamespace(
            namespace_utf8=value.effect.namespace_utf8,
            repository=value.effect.repository,
        )
        self.calls = 0
        self.failure = False
        self.changed = False

    async def read_copy(self, acceptance_id):
        self.calls += 1
        assert acceptance_id == self.value.effect.acceptance.acceptance_id
        if self.failure:
            raise RuntimeError("secret upstream detail")
        raw = self.value.wire + b" " if self.changed else self.value.wire
        return ProtectedCopyRead(self.value.effect, raw)


class Basis:
    def __init__(self, value, profile):
        self.value, self.profile = value, profile
        self.calls = 0
        self.status = "pending"
        self.failure = False
        self.revised = False

    async def read_basis(self, effect):
        self.calls += 1
        if self.failure:
            raise RuntimeError("private native detail")
        data = basis_document(effect, status=self.status)
        if self.revised:
            data["work"]["edit_revision"] = (
                data["work"]["edit_revision"].split("@description:")[0]
                + "@description:v99@sha256:"
                + "9" * 64
            )
            data["work"]["item_revision"] = data["work"]["edit_revision"] + "@revise:99"
        return decode_current_basis(canonical(data), effect=effect)


class FakeGit:
    def __init__(self, value):
        self.value, self.profile = value, value.profile
        self.prepares, self.verifies = 0, 0
        self.failure = False

    async def prepare_original(self, effect, record):
        self.prepares += 1
        bound, registration = git_module._record(effect, record, self.profile)
        message = git_module._message(effect, record, bound)
        parent = json.loads(effect.parameters_utf8)["base_commit"]
        raw = git_module._local(
            effect,
            record,
            registration,
            "a" * len(parent),
            "b" * len(parent),
            message,
            "sha256:" + "c" * 64,
        )
        return git_module.GitArtifact(
            raw, "artifacts/" + record.effect_key + "/original.bundle"
        )

    async def verify_original(self, effect, record):
        self.verifies += 1
        if self.failure:
            raise git_module.ProtectedGitRefused("protected-git-refused")
        raw = next(
            e.payload_utf8 for e in record.events if e.event_type == "LOCAL_READY"
        )
        return git_module.GitArtifact(raw, json.loads(raw)["artifact_relative_path"])


class Provider:
    def __init__(self, value, profile):
        self.value, self.profile = value, profile
        self.calls = []
        self.branch = self.pr = False
        self.branch_status = self.pr_status = None
        self.pr_state = "open"
        self.capability = "CAPABILITY_PRESENT"
        self.lost = set()
        self.delay = 0
        self.fault = None
        self.tamper = None

    def result(self, record, operation, status, observation=None):
        request = module._request(record, self.value.effect, operation)
        result = SimpleNamespace(
            operation=operation,
            effect_key=record.effect_key,
            binding_sha256=sha(record.binding_utf8),
            request_sha256=sha(request),
            status=status,
            observation_utf8=observation,
        )
        if self.tamper is not None:
            setattr(result, self.tamper, "different")
        return result

    def observation(self, record, operation):
        req = json.loads(module._request(record, self.value.effect, operation))
        fields = journal_module._PAYLOAD_FIELDS[
            "BRANCH_OBSERVED" if operation == "branch-lookup" else "PR_OBSERVED"
        ].split()
        observed = {k: v for k, v in req.items() if k in fields and k != "schema"}
        observed.update(
            schema="vuoro-protected-branch-observation/v1"
            if operation == "branch-lookup"
            else "vuoro-protected-pr-observation/v1",
            request_sha256=sha(canonical(req)),
        )
        if operation == "pr-lookup":
            observed.update(
                pr_number=7,
                pr_url="https://forge.example.invalid/pulls/7",
                state=self.pr_state,
            )
        return canonical(observed)

    async def lookup_branch(self, record):
        self.calls.append("branch-lookup")
        await asyncio.sleep(self.delay)
        status = self.branch_status or ("OBSERVED" if self.branch else "ABSENT")
        return self.result(
            record,
            "branch-lookup",
            status,
            self.observation(record, "branch-lookup") if status == "OBSERVED" else None,
        )

    async def lookup_pr(self, record):
        self.calls.append("pr-lookup")
        await asyncio.sleep(self.delay)
        status = self.pr_status or ("OBSERVED" if self.pr else "ABSENT")
        return self.result(
            record,
            "pr-lookup",
            status,
            self.observation(record, "pr-lookup") if status == "OBSERVED" else None,
        )

    async def fresh_capability(self, record):
        self.calls.append("capability-check")
        return self.result(record, "capability-check", self.capability)

    async def create_branch_once(self, record, original):
        self.calls.append("branch-create")
        assert original.local_ready_utf8 == record.events[1].payload_utf8
        if "branch-create" not in self.lost:
            self.branch = True
        if self.fault:
            self.fault("branch-create")
        if "branch-create" in self.lost:
            raise RuntimeError("ack lost")
        return self.result(record, "branch-create", "TRANSMISSION_POSSIBLE")

    async def create_pr_once(self, record):
        self.calls.append("pr-create")
        if "pr-create" not in self.lost:
            self.pr = True
        if self.fault:
            self.fault("pr-create")
        if "pr-create" in self.lost:
            raise RuntimeError("ack lost")
        return self.result(record, "pr-create", "TRANSMISSION_POSSIBLE")


@pytest.fixture
def setup(tmp_path, keys):
    value = git_fixtures.case(tmp_path, keys)
    value.journal.close()
    directory = tmp_path / "executor-journal"
    journal_module.install_journal(
        directory, host_id=git_fixtures.HOST, registry_utf8=git_fixtures.REGISTRY
    )
    store = journal_module.ExecutionJournal(
        directory, host_id=git_fixtures.HOST, registry_utf8=git_fixtures.REGISTRY
    )
    value.journal, value.directory = store, directory
    ledger = Ledger(value)
    native = Basis(value, ledger.profile)
    profile = Profile(
        value.profile,
        canonical(
            {"effect_repository": value.effect.repository, "base_branch": "main"}
        ),
        sha(store.registry_utf8),
    )
    provider = Provider(value, profile)
    git = FakeGit(value)
    executor = ProtectedExecutor(ledger, native, store, git, provider)
    result = SimpleNamespace(
        value=value,
        ledger=ledger,
        native=native,
        provider=provider,
        git=git,
        executor=executor,
    )
    yield result
    value.journal.close()


def execute(value):
    return asyncio.run(value.executor.run(value.value.effect.acceptance.acceptance_id))


def record(value):
    with value.value.journal.cycle():
        return value.value.journal.read(journal_module.effect_key(value.value.effect))


def test_exact_publication_outcome_is_durable_and_terminal_has_no_new_reads_or_sends(
    setup,
):
    value = setup
    result = execute(value)
    assert (
        result.status == "APPLIED" and result.outcome_utf8 == record(value).outcome_utf8
    )
    assert value.git.prepares == 1
    assert (
        value.provider.calls.count("branch-create")
        == value.provider.calls.count("pr-create")
        == 1
    )
    nativecalls = value.native.calls
    providercalls = list(value.provider.calls)
    value.native.failure = True
    assert execute(value) == result
    assert value.native.calls == nativecalls and value.provider.calls == providercalls
    value.ledger.failure = True
    assert execute(value).status == "DEFERRED"
    assert record(value).outcome_utf8 == result.outcome_utf8


@pytest.mark.parametrize("status", ["blocked", "done", "revised"])
def test_non_ready_before_any_marker_never_transmits_or_prepares(setup, status):
    value = setup
    if status == "revised":
        value.native.revised = True
    else:
        value.native.status = status
    assert execute(value).status == "DEFERRED"
    assert (
        not value.provider.calls and value.git.prepares == 0 and not record(value).spent
    )


@pytest.mark.parametrize("operation", ["branch-create", "pr-create"])
def test_ambiguous_absent_never_resends_and_late_exact_success_can_be_adopted(
    setup, operation
):
    value = setup
    value.provider.lost.add(operation)
    first = execute(value)
    assert first.status == "UNSETTLED"
    for _ in range(3):
        assert execute(value).status == "UNSETTLED"
    assert value.provider.calls.count(operation) == 1
    if operation == "branch-create":
        value.provider.branch = True
    else:
        value.provider.pr = True
    assert execute(value).status == "APPLIED"
    assert value.provider.calls.count(operation) == 1


@pytest.mark.parametrize("status", ["blocked", "done", "revised"])
def test_non_ready_can_settle_only_requests_already_spent(setup, status):
    value = setup
    value.provider.lost.add("pr-create")
    assert execute(value).status == "UNSETTLED"
    value.provider.pr = True
    if status == "revised":
        value.native.revised = True
    else:
        value.native.status = status
    assert execute(value).status == "APPLIED"
    assert (
        value.provider.calls.count("branch-create")
        == value.provider.calls.count("pr-create")
        == 1
    )


def test_non_ready_after_only_branch_marker_does_not_arm_pr(setup):
    value = setup
    value.provider.lost.add("branch-create")
    assert execute(value).status == "UNSETTLED"
    value.provider.branch = True
    value.native.status = "done"
    assert execute(value).status == "UNSETTLED"
    assert not any(s.operation == "pr-create" for s in record(value).spent)
    assert "pr-create" not in value.provider.calls


def test_first_durable_closed_pr_observation_refuses_without_reopening(setup):
    value = setup
    value.provider.pr_state = "closed"
    original = execute(value)
    assert original.status == "REFUSED"
    value.provider.pr_state = "open"
    assert execute(value) == original
    assert value.provider.calls.count("pr-create") == 1


@pytest.mark.parametrize("operation", ["branch-create", "pr-create"])
def test_spent_request_exact_mismatch_becomes_immutable_conflict(setup, operation):
    value = setup
    value.provider.lost.add(operation)
    assert execute(value).status == "UNSETTLED"
    if operation == "branch-create":
        value.provider.branch_status = "CONFLICT"
    else:
        value.provider.pr_status = "CONFLICT"
    assert execute(value).status == "CONFLICT"
    assert execute(value).status == "CONFLICT"
    assert value.provider.calls.count(operation) == 1


@pytest.mark.parametrize(
    "field",
    [
        "operation",
        "effect_key",
        "binding_sha256",
        "request_sha256",
        "status",
        "observation_utf8",
    ],
)
def test_fabricated_provider_result_cannot_arm_or_settle(setup, field):
    value = setup
    value.provider.tamper = field
    assert execute(value).status == "DEFERRED"
    assert not record(value).spent
    assert "branch-create" not in value.provider.calls


def test_basis_outage_and_current_scope_failure_do_not_poison_spent_request(setup):
    value = setup
    value.provider.lost.add("branch-create")
    assert execute(value).status == "UNSETTLED"
    before = record(value)
    value.native.failure = True
    failure = execute(value)
    assert failure.status == "UNSETTLED" and failure.effect_key == before.effect_key
    assert record(value) == before
    value.native.failure = False
    value.ledger.failure = True
    assert execute(value).status == "DEFERRED"
    assert record(value) == before


def test_cooperative_tasks_share_one_journal_cycle_and_only_one_send(setup):
    value = setup
    value.provider.delay = 0.01
    second = ProtectedExecutor(
        value.ledger, value.native, value.value.journal, value.git, value.provider
    )

    async def workflow():
        return await asyncio.gather(
            value.executor.run(value.value.effect.acceptance.acceptance_id),
            second.run(value.value.effect.acceptance.acceptance_id),
        )

    assert [v.status for v in asyncio.run(workflow())] == ["APPLIED", "APPLIED"]
    assert (
        value.provider.calls.count("branch-create")
        == value.provider.calls.count("pr-create")
        == 1
    )


@pytest.mark.parametrize("change", ["copy", "capability", "basis"])
def test_final_barrier_is_fresh_and_runs_after_local_verification_and_prelookup(
    setup, change
):
    value = setup
    old = value.provider.lookup_branch

    async def preliminary(rec):
        result = await old(rec)
        if change == "copy":
            value.ledger.changed = True
        elif change == "capability":
            value.provider.capability = "REFUSED"
        else:
            value.native.status = "blocked"
        return result

    value.provider.lookup_branch = preliminary
    assert execute(value).status == "DEFERRED"
    assert not record(value).spent and "branch-create" not in value.provider.calls
    assert value.git.prepares == 1 and value.git.verifies == 1
    assert value.ledger.calls == 2


@pytest.mark.parametrize("change", ["profile", "registry", "signing", "refset"])
def test_permanent_profile_and_original_copy_drift_cannot_prepare_or_mutate(
    setup, change
):
    value = setup
    value.provider.capability = "REFUSED"
    assert execute(value).status == "DEFERRED"
    before = record(value)
    if change == "profile":
        value.provider.profile = Profile(
            value.value.profile,
            canonical(
                {
                    "effect_repository": value.value.effect.repository,
                    "base_branch": "other",
                }
            ),
            sha(value.value.journal.registry_utf8),
        )
    elif change == "registry":
        value.value.journal.registry_utf8 = canonical({"schema": "other"})
    elif change == "signing":
        value.git.profile = git_fixtures.profile(
            value.value.effect,
            {"public": value.value.profile.public_key_utf8, "signing": "f" * 40},
        )
    else:
        value.ledger.changed = True
    assert execute(value).status == "DEFERRED"
    assert "branch-create" not in value.provider.calls
    if change != "registry":
        assert record(value) == before
    else:
        value.value.journal.registry_utf8 = git_fixtures.REGISTRY


def test_actual_journal_commit_then_fsync_uncertainty_never_invokes_mutation(
    setup, monkeypatch
):
    value = setup
    real = value.value.journal.mark_spent

    def uncertain(*args):
        assert real(*args) is True
        value.value.journal._uncertain = True
        raise journal_module.JournalRefused("execution-journal-invalid")

    monkeypatch.setattr(value.value.journal, "mark_spent", uncertain)
    failure = execute(value)
    assert (
        failure.status == "UNSETTLED"
        and failure.effect_key == journal_module.effect_key(value.value.effect)
    )
    assert "branch-create" not in value.provider.calls
    value.value.journal.close()
    value.value.journal = journal_module.ExecutionJournal(
        value.value.directory,
        host_id=git_fixtures.HOST,
        registry_utf8=git_fixtures.REGISTRY,
    )
    value.executor = ProtectedExecutor(
        value.ledger, value.native, value.value.journal, value.git, value.provider
    )
    assert execute(value).status == "UNSETTLED"
    assert "branch-create" not in value.provider.calls


class FilesystemProvider(Provider):
    def __init__(self, value, profile, state, crash=None):
        super().__init__(value, profile)
        self.state = Path(state)
        self.crash = crash

    def load(self):
        if self.state.exists():
            data = json.loads(self.state.read_bytes())
            self.branch, self.pr = data["branch"], data["pr"]

    def save(self):
        path = self.state.with_suffix(".pending")
        with path.open("wb") as output:
            output.write(canonical({"branch": self.branch, "pr": self.pr}))
            output.flush()
            os.fsync(output.fileno())
        os.replace(path, self.state)
        fd = os.open(self.state.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def count(self, operation):
        with self.state.with_suffix(".calls").open("ab") as output:
            output.write(operation.encode() + b"\n")
            output.flush()
            os.fsync(output.fileno())

    async def lookup_branch(self, record):
        self.load()
        return await super().lookup_branch(record)

    async def lookup_pr(self, record):
        self.load()
        return await super().lookup_pr(record)

    async def create_branch_once(self, record, original):
        self.count("branch-create")
        result = await super().create_branch_once(record, original)
        self.save()
        if self.crash == "branch-send":
            hard_crash()
        return result

    async def create_pr_once(self, record):
        self.count("pr-create")
        result = await super().create_pr_once(record)
        self.save()
        if self.crash == "pr-send":
            hard_crash()
        return result


def hard_crash():
    import signal

    os.kill(os.getpid(), signal.SIGKILL)


def real_composition(value, state, crash=None):
    store = journal_module.ExecutionJournal(
        value.value.directory,
        host_id=git_fixtures.HOST,
        registry_utf8=git_fixtures.REGISTRY,
    )

    class CountingSigner:
        async def sign(self, unsigned):
            with Path(state).with_suffix(".signs").open("ab") as output:
                output.write(b"sign\n")
                output.flush()
                os.fsync(output.fileno())
            return await value.value.signer.sign(unsigned)

    actual = git_module.ProtectedGit(
        value.value.profile,
        value.value.directory,
        value.value.base_provider,
        CountingSigner(),
    )
    provider = FilesystemProvider(value.value, value.provider.profile, state, crash)
    executor = ProtectedExecutor(value.ledger, value.native, store, actual, provider)
    return store, actual, provider, executor


def child_cycle(value, state, crash, result_path=None):
    store, actual, provider, executor = real_composition(value, state, crash)
    hooks = {
        "bound": "bind",
        "local-ready": "record_local_ready",
        "branch-marker": "mark_spent",
        "pr-marker": "mark_spent",
        "branch-observed": "record_observed",
        "pr-observed": "record_observed",
        "applied": "record_outcome",
    }
    if crash in hooks:
        method = hooks[crash]
        old = getattr(store, method)

        def wrapped(*args, **kwargs):
            result = old(*args, **kwargs)
            matching = (
                crash
                not in {"branch-marker", "pr-marker", "branch-observed", "pr-observed"}
                or args[1]
                == {
                    "branch-marker": "branch-create",
                    "pr-marker": "pr-create",
                    "branch-observed": "BRANCH_OBSERVED",
                    "pr-observed": "PR_OBSERVED",
                }[crash]
            )
            if matching:
                hard_crash()
            return result

        setattr(store, method, wrapped)
    if crash == "artifact":
        old = actual.prepare_original

        async def prepare(*args):
            await old(*args)
            hard_crash()

        actual.prepare_original = prepare
    try:
        result = asyncio.run(executor.run(value.value.effect.acceptance.acceptance_id))
        if result_path:
            Path(result_path).write_text(result.status)
    finally:
        store.close()


@pytest.mark.parametrize(
    "boundary",
    [
        "bound",
        "artifact",
        "local-ready",
        "branch-marker",
        "branch-send",
        "branch-observed",
        "pr-marker",
        "pr-send",
        "pr-observed",
        "applied",
    ],
)
def test_actual_git_and_journal_hard_crash_restart_at_each_durable_boundary(
    setup, tmp_path, boundary
):
    value = setup
    state = tmp_path / "remote.json"
    child = multiprocessing.get_context("fork").Process(
        target=child_cycle, args=(value, state, boundary)
    )
    child.start()
    child.join(20)
    assert child.exitcode == -9
    store, actual, provider, executor = real_composition(value, state)
    try:
        result = asyncio.run(executor.run(value.value.effect.acceptance.acceptance_id))
        expected = (
            "UNSETTLED" if boundary in {"branch-marker", "pr-marker"} else "APPLIED"
        )
        assert result.status == expected
        again = asyncio.run(executor.run(value.value.effect.acceptance.acceptance_id))
        assert again.status == expected
        calls = (
            state.with_suffix(".calls").read_text().splitlines()
            if state.with_suffix(".calls").exists()
            else []
        )
        assert calls.count("branch-create") <= 1 and calls.count("pr-create") <= 1
        assert state.with_suffix(".signs").read_text().splitlines() == ["sign"]
        if boundary == "branch-marker":
            assert not calls
        if boundary == "pr-marker":
            assert calls == ["branch-create"]
    finally:
        store.close()


@pytest.mark.parametrize("later", ["closed", "edited", "outage"])
def test_historical_open_pr_proof_after_crash_finalizes_without_lifecycle_or_basis_reads(
    setup, tmp_path, later
):
    value = setup
    state = tmp_path / "remote.json"
    child = multiprocessing.get_context("fork").Process(
        target=child_cycle, args=(value, state, "pr-observed")
    )
    child.start()
    child.join(20)
    assert child.exitcode == -9
    store, actual, provider, executor = real_composition(value, state)
    provider.pr_state = "closed"
    provider.pr_status = "CONFLICT" if later == "edited" else "UNKNOWN"
    value.native.failure = True
    try:
        assert (
            asyncio.run(
                executor.run(value.value.effect.acceptance.acceptance_id)
            ).status
            == "APPLIED"
        )
        assert not provider.calls and value.native.calls == 0
        assert state.with_suffix(".calls").read_text().splitlines() == [
            "branch-create",
            "pr-create",
        ]
    finally:
        store.close()


def test_two_actual_processes_share_installed_journal_and_emit_one_operation_each(
    setup, tmp_path
):
    value = setup
    state = tmp_path / "remote.json"
    ctx = multiprocessing.get_context("fork")
    outputs = [tmp_path / "result1", tmp_path / "result2"]
    processes = [
        ctx.Process(target=child_cycle, args=(value, state, None, path))
        for path in outputs
    ]
    for child in processes:
        child.start()
    for child in processes:
        child.join(20)
        assert child.exitcode == 0
    assert [path.read_text() for path in outputs] == ["APPLIED", "APPLIED"]
    assert state.with_suffix(".calls").read_text().splitlines() == [
        "branch-create",
        "pr-create",
    ]


@pytest.mark.parametrize("stage", ["database", "directory"])
def test_actual_postcommit_fsync_fault_withholds_send_and_restart_never_rearms(
    setup, monkeypatch, stage
):
    value = setup
    store = value.value.journal
    real_fsync = journal_module.os.fsync
    failures = []

    def fsync(fd):
        real_fsync(fd)
        target = store._database_fd if stage == "database" else store._directory_fd
        if fd == target and store._connection is not None:
            markers = store._connection.execute(
                "SELECT COUNT(*) FROM journal_events WHERE event_type='BRANCH_TRANSMISSION_POSSIBLE'"
            ).fetchone()[0]
            if markers and not failures:
                failures.append(True)
                raise OSError("uncertain local persistence")

    monkeypatch.setattr(journal_module.os, "fsync", fsync)
    failure = execute(value)
    assert (
        failure.status == "UNSETTLED"
        and failure.effect_key == journal_module.effect_key(value.value.effect)
    )
    assert failures and store._uncertain
    assert "branch-create" not in value.provider.calls
    monkeypatch.setattr(journal_module.os, "fsync", real_fsync)
    store.close()
    value.value.journal = journal_module.ExecutionJournal(
        value.value.directory,
        host_id=git_fixtures.HOST,
        registry_utf8=git_fixtures.REGISTRY,
    )
    value.executor = ProtectedExecutor(
        value.ledger, value.native, value.value.journal, value.git, value.provider
    )
    assert execute(value).status == "UNSETTLED"
    assert "branch-create" not in value.provider.calls


def test_advisory_failure_after_durable_applied_cannot_repeat_publication(setup):
    value = setup
    result = execute(value)
    assert result.status == "APPLIED"
    calls = list(value.provider.calls)
    with pytest.raises(RuntimeError):
        raise RuntimeError("separate optional advisory writer failed")
    assert execute(value).status == "APPLIED"
    assert (
        value.provider.calls == calls
        and record(value).outcome_utf8 == result.outcome_utf8
    )


@pytest.mark.parametrize("status", ["OBSERVED", "CONFLICT", "UNKNOWN", "REFUSED"])
def test_no_unrecorded_branch_adoption_or_marker_from_preliminary_lookup(setup, status):
    value = setup
    value.provider.branch_status = status
    assert execute(value).status == "DEFERRED"
    assert not record(value).spent
    assert "branch-create" not in value.provider.calls


def test_task_cancellation_after_send_preserves_marker_and_recovers_without_resend(
    setup,
):
    value = setup
    reached = None
    old = value.provider.create_branch_once

    async def send(record, artifact):
        result = await old(record, artifact)
        reached.set()
        await asyncio.Event().wait()
        return result

    value.provider.create_branch_once = send

    async def workflow():
        nonlocal reached
        reached = asyncio.Event()
        task = asyncio.create_task(
            value.executor.run(value.value.effect.acceptance.acceptance_id)
        )
        await reached.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        value.provider.create_branch_once = old
        return await value.executor.run(value.value.effect.acceptance.acceptance_id)

    assert asyncio.run(workflow()).status == "APPLIED"
    assert (
        value.provider.calls.count("branch-create")
        == value.provider.calls.count("pr-create")
        == 1
    )


def test_actual_original_oid_drift_refuses_before_any_provider_operation(
    setup, tmp_path
):
    value = setup
    state = tmp_path / "remote.json"
    store, actual, provider, executor = real_composition(value, state)
    try:
        with store.cycle():
            bound = executor._bind(
                ProtectedCopyRead(value.value.effect, value.value.wire)
            )
            artifact = asyncio.run(actual.prepare_original(value.value.effect, bound))
            ready = json.loads(artifact.local_ready_utf8)
            original = ready["commit_oid"]
            ready["commit_oid"] = ("0" if original[0] != "0" else "1") + original[1:]
            store.record_local_ready(bound.effect_key, canonical(ready))
        assert (
            asyncio.run(
                executor.run(value.value.effect.acceptance.acceptance_id)
            ).status
            == "CONFLICT"
        )
        assert not provider.calls
        with store.cycle():
            assert store.read(bound.effect_key).events[-1].event_type == "CONFLICT"
    finally:
        store.close()
