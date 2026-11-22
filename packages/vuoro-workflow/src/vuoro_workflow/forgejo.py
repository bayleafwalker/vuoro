"""Read scoped Actions facts without keeping credentials or raw event payloads."""

import json
import os
import subprocess
import sys
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PENDING = {"waiting", "running", "blocked", "pending", "queued"}
FAILED = {"failure", "cancelled", "canceled", "skipped", "timed_out"}
TERMINAL = {"success", "failure", "superseded"}
MAX_BYTES = 16 * 1024 * 1024


class Unavailable(Exception):
    """The provider did not return a complete, understood observation."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def broker_get(url):
    """Exec only inside a repository-scoped broker child."""
    token = os.environ.get("FJ_TOKEN")
    if not token:
        raise Unavailable("scoped Forgejo credential missing")
    request = Request(url, headers={"Authorization": f"token {token}"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            payload = response.read(MAX_BYTES + 1)
    except (URLError, TimeoutError) as exc:
        raise Unavailable("Forgejo read failed") from exc
    if len(payload) > MAX_BYTES:
        raise Unavailable("Forgejo response exceeded size bound")
    return json.loads(payload)


def scoped_read(spec, path):
    url = f"{spec['origin']}/api/v1/repos/{spec['repository']}/{path}"
    return broker_get(url)


def observe_scoped(spec):
    """Use one scoped credential for a consistent set of provider reads."""
    try:
        result = subprocess.run(
            [
                "credctl", "exec", "repo.read", "--repository",
                f"forgejo:{spec['repository']}", "--",
                sys.executable, "-m", "vuoro_workflow.cli", "_forge-observe",
            ],
            input=json.dumps(spec), text=True, capture_output=True,
            timeout=90, check=False,
        )
        if result.returncode:
            raise Unavailable("scoped Forgejo read failed")
        observation = json.loads(result.stdout)
        if observation["state"] == "unavailable":
            raise Unavailable(observation["reason"])
        return observation
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError) as exc:
        raise Unavailable("Forgejo response unavailable") from exc


def jobs_for(spec, read):
    """Handle both the unpaged Forgejo jobs list and page-sensitive variants."""
    jobs = {}
    for page in range(1, 101):
        batch = read(spec, f"actions/runs/{spec['run']}/jobs?page={page}&limit=50")
        if not isinstance(batch, list):
            raise Unavailable("Forgejo jobs response is not a list")
        if not batch:
            break
        identities = {job["id"] for job in batch}
        if len(identities) != len(batch):
            raise Unavailable("duplicate Forgejo job identity within a page")
        if page == 2 and identities == jobs.keys() and all(
            jobs[job["id"]] == job for job in batch
        ):
            # Older Forgejo versions ignore pagination for this endpoint.
            break
        if identities & jobs.keys():
            raise Unavailable("Forgejo jobs pages changed during observation")
        jobs.update((job["id"], job) for job in batch)
    else:
        raise Unavailable("Forgejo jobs pagination exceeded bound")
    return list(jobs.values())


def observe(spec, read=scoped_read):
    """Return only typed, identity-bound evidence needed by the reconciler."""
    try:
        if spec["pr"]:
            pr = read(spec, f"pulls/{spec['pr']}")
            if (
                pr["number"] != spec["pr"]
                or pr["base"]["repo"]["full_name"] != spec["repository"]
            ):
                raise Unavailable("Forgejo pull request identity mismatch")
            if (
                pr["head"]["sha"] != spec["head"]
                or pr["base"]["ref"] != spec["base"]
                or pr["state"] != "open"
            ):
                return {"state": "superseded", "attempt": 0}
        path = f"actions/runs/{spec['run']}"
        run = read(spec, path)
        expected_ref = f"#{spec['pr']}" if spec["pr"] else spec["ref"]
        if (
            run["id"] != spec["run"]
            or run["repository"]["full_name"] != spec["repository"]
            or run["workflow_id"] != spec["workflow"]
            or run["commit_sha"] != spec["head"]
            or run["trigger_event"] != spec["event"]
            or run["prettyref"] != expected_ref
        ):
            raise Unavailable("Forgejo workflow identity mismatch")
        if spec["kind"] == "promotion":
            event = json.loads(run["event_payload"])
            if (
                event["ref"] != f"refs/tags/{spec['ref']}"
                or event["inputs"][spec["digest_input"]] != spec["digest"]
            ):
                raise Unavailable("Forgejo promotion ref or digest mismatch")
        jobs = jobs_for(spec, read)
        # A second read excludes a rerun or state transition during pagination.
        refreshed = read(spec, path)
        if any(
            refreshed[key] != run[key]
            for key in ("id", "commit_sha", "status", "updated")
        ):
            raise Unavailable("Forgejo run changed during observation")
        if spec["pr"]:
            refreshed_pr = read(spec, f"pulls/{spec['pr']}")
            if any(refreshed_pr[key] != pr[key] for key in ("head", "base", "state")):
                raise Unavailable("Forgejo pull request changed during observation")
        if any(
            job["run_id"] != spec["run"]
            or not isinstance(job["attempt"], int)
            or job["attempt"] < 1
            for job in jobs
        ):
            raise Unavailable("Forgejo job identity mismatch")
        attempt = max((job["attempt"] for job in jobs), default=0)
        latest = [job for job in jobs if job["attempt"] == attempt]
        required = set(spec["required"])
        checks = {job["name"]: job["status"] for job in latest if job["name"] in required}
        if len(checks) != sum(job["name"] in required for job in latest):
            raise Unavailable("multiple jobs match a required check")
        status = run["status"]
        if status not in PENDING | FAILED | {"success"}:
            raise Unavailable("unknown Forgejo workflow state")
        if any(value not in PENDING | FAILED | {"success"} for value in checks.values()):
            raise Unavailable("unknown Forgejo check state")
        if status in FAILED:
            state = "failure"
        elif status == "success" and required == checks.keys() and all(
            value == "success" for value in checks.values()
        ):
            state = "success"
        elif status == "success":
            raise Unavailable("successful run lacks exact-attempt required check evidence")
        else:
            state = "pending"
        return {"state": state, "attempt": attempt, "checks": checks, "run_status": status}
    except (TypeError, ValueError, KeyError) as exc:
        raise Unavailable("malformed Forgejo observation") from exc
