import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from vuoro_workflow import forgejo, store
from vuoro_workflow.cli import main

REPOSITORY = "test/project"
HEAD = "a" * 40
THREAD = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"


@pytest.fixture
def spec():
    return {
        "kind": "ci", "origin": "https://forge.example.org",
        "repository": REPOSITORY, "workflow": "ci.yaml", "event": "pull_request",
        "run": 42, "head": HEAD, "pr": 13, "base": "main", "ref": None,
        "required": ["gate", "test"], "thread": THREAD, "environment": None,
        "digest": None,
    }


@pytest.fixture
def forge():
    repository = {"full_name": REPOSITORY}
    run = {
        "id": 42, "repository": repository, "workflow_id": "ci.yaml",
        "commit_sha": HEAD, "prettyref": "#13", "trigger_event": "pull_request",
        "status": "success", "updated": "2026-11-22T05:00:00Z",
    }
    pr = {
        "number": 13, "base": {"ref": "main", "repo": repository},
        "head": {"sha": HEAD}, "state": "open",
    }
    jobs = [
        {"id": 1, "name": "test", "run_id": 42, "attempt": 1, "status": "success"},
        {"id": 2, "name": "gate", "run_id": 42, "attempt": 1, "status": "success"},
    ]

    def read(spec, path):
        if path.startswith("pulls/"):
            return pr
        if "/jobs?" in path:
            return jobs if "page=1&" in path else []
        return run

    return run, pr, jobs, read


def test_live_required_check_reducer(spec, forge):
    run, _, jobs, read = forge
    assert forgejo.observe(spec, read)["state"] == "success"
    run["status"] = "running"
    jobs[1]["status"] = "blocked"
    assert forgejo.observe(spec, read)["state"] == "pending"
    run["status"] = "success"
    with pytest.raises(forgejo.Unavailable, match="required check evidence"):
        forgejo.observe(spec, read)
    run["status"] = "failure"
    assert forgejo.observe(spec, read)["state"] == "failure"


def test_attempt_latest_only(spec, forge):
    run, _, jobs, read = forge
    jobs.append({**jobs[0], "id": 3, "attempt": 2})
    with pytest.raises(forgejo.Unavailable, match="required check evidence"):
        forgejo.observe(spec, read)
    jobs.append({**jobs[1], "id": 4, "attempt": 2})
    assert forgejo.observe(spec, read)["attempt"] == 2


@pytest.mark.parametrize(("key", "value"), [
    ("commit_sha", "b" * 40), ("workflow_id", "other.yaml"),
    ("trigger_event", "push"), ("prettyref", "#14"),
    ("repository", {"full_name": "test/foreign"}),
])
def test_foreign_run_refused(spec, forge, key, value):
    run, _, _, read = forge
    run[key] = value
    with pytest.raises(forgejo.Unavailable, match="identity mismatch"):
        forgejo.observe(spec, read)


def test_moved_pr_returns_superseded(spec, forge):
    _, pr, _, read = forge
    pr["head"]["sha"] = "b" * 40
    assert forgejo.observe(spec, read)["state"] == "superseded"


def test_older_and_paged_jobs_endpoints(spec, forge):
    _, _, jobs, _ = forge
    large = [*jobs, *({**jobs[0], "id": n, "name": f"job-{n}"} for n in range(3, 75))]
    assert forgejo.jobs_for(spec, lambda *_: large) == large

    def paged(_, path):
        return large[:50] if "page=1&" in path else large[50:]

    assert forgejo.jobs_for(spec, paged) == large


def test_empty_response_does_not_pass(spec, forge):
    _, _, jobs, read = forge
    jobs.clear()
    with pytest.raises(forgejo.Unavailable, match="required check evidence"):
        forgejo.observe(spec, read)


def test_promotion_digest_and_tag_are_bound(spec, forge):
    run, _, _, read = forge
    digest = "sha256:" + "b" * 64
    spec.update(
        kind="promotion", workflow="promote.yaml", event="workflow_dispatch",
        pr=None, ref="v1", environment="staging", digest=digest, digest_input="image_digest",
    )
    run.update(
        workflow_id="promote.yaml", trigger_event="workflow_dispatch", prettyref="v1",
        event_payload=json.dumps({"ref": "refs/tags/v1", "inputs": {"image_digest": digest}}),
    )
    assert forgejo.observe(spec, read)["state"] == "success"
    spec["digest"] = "sha256:" + "c" * 64
    with pytest.raises(forgejo.Unavailable, match="promotion ref or digest"):
        forgejo.observe(spec, read)


def test_outbox_dedupe_ack_and_restart(tmp_path, spec):
    path = tmp_path / "state.sqlite"
    db = store.connect(path)
    watch = store.register(db, spec, 120)
    assert store.register(db, spec, 120) == watch
    store.reconcile(db, lambda _: {"state": "success", "attempt": 1})
    event, = store.inbox(db, THREAD)
    assert event["watch"] == watch
    assert not store.acknowledge(db, OTHER, event["id"])
    db.close()
    with store.connect(path) as db:
        store.reconcile(db, lambda _: {"state": "success", "attempt": 1})
        assert store.inbox(db, THREAD) == [event]
        assert store.acknowledge(db, THREAD, event["id"])
        store.reconcile(db, lambda _: {"state": "success", "attempt": 1})
        assert store.inbox(db, THREAD) == []
        store.reconcile(db, lambda _: {"state": "failure", "attempt": 2})
        assert [(row["state"], row["attempt"]) for row in store.inbox(db, THREAD)] == [
            ("failure", 2)
        ]


def test_observation_unavailable_leaves_no_event(tmp_path, spec):
    def fail(_):
        raise forgejo.Unavailable("provider refused request")

    with store.connect(tmp_path / "state.sqlite") as db:
        store.register(db, spec, 120)
        store.reconcile(db, fail)
        row, = store.status(db)
        assert row["observation"]["state"] == "unavailable"
        assert store.inbox(db, THREAD) == []


def test_concurrent_reconciler_lease_and_stopped_watch(tmp_path, spec):
    path = tmp_path / "state.sqlite"
    db = store.connect(path)
    watch = store.register(db, spec, 120)
    started, finish = Event(), Event()

    def ongoing(_):
        started.set()
        assert finish.wait(5)
        return {"state": "success", "attempt": 1}

    def work():
        with store.connect(path) as another:
            store.reconcile(another, ongoing)

    with ThreadPoolExecutor() as pool:
        task = pool.submit(work)
        assert started.wait(5)
        try:
            store.reconcile(db, lambda _: pytest.fail("observed while another lease was active"))
            assert store.stop(db, watch)
        finally:
            finish.set()
        task.result()
    assert store.inbox(db, THREAD) == []


def test_lease_can_be_recovered_after_observer_crash(tmp_path, spec):
    with store.connect(tmp_path / "state.sqlite") as db:
        watch = store.register(db, spec, 120)
        with db:
            db.execute("UPDATE watches SET lease = 'old', lease_expires = ? WHERE id = ?",
                       (time.time() - 1, watch))
        store.reconcile(db, lambda _: {"state": "failure", "attempt": 1})
        assert len(store.inbox(db, THREAD)) == 1


def test_cli_uses_exact_thread_and_serializes(tmp_path, capsys):
    common = ["--state", str(tmp_path / "state.sqlite"), "--json"]
    assert main([
        *common, "watch", "--origin", "https://forge.example.org", "--repository", REPOSITORY,
        "--head", HEAD, "--thread", THREAD, "--pr", "13", "--base", "main", "--run", "42",
        "--workflow", "ci.yaml", "--event", "pull_request", "--required", "gate",
    ]) == 0
    watch = json.loads(capsys.readouterr().out)
    assert main([*common, "status"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == watch["watch"]
