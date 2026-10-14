"""vuoro-ops migration-preflight recovery classes and recovery-export."""

from __future__ import annotations

from pathlib import Path

import pytest

from vuoro_service.ops import cli
from vuoro_service.ops.migration import migration_preflight, recovery_class, startup_proof
from vuoro_service.ops.recovery import recovery_export

PINS = Path(__file__).resolve().parents[1] / "composition" / "adapter-pins.json"
CANDIDATE, PRIOR = "img@sha256:" + "c" * 64, "img@sha256:" + "p" * 64


def runner_for(ranges):
    def run(image, script, env):
        assert "schema_ranges" in script and env == {}
        if ranges[image] is None:
            from vuoro_service.ops.runtime import ProbeError

            raise ProbeError("cannot pull")
        low, high = ranges[image]
        return {"service_version": image[-3:], "distributions": {}, "manifest_sha256": "0" * 64,
                "attestation": {"verified": True}, "schema_ranges": {"work": {"minimum": low, "maximum": high}}}

    return run


P22, P2425 = {"minimum": 22, "maximum": 22}, {"minimum": 24, "maximum": 25}


@pytest.mark.parametrize(
    ("current", "target", "prior", "job", "expected"),
    [
        (22, 25, P22, "not-created", "cancel"),
        (22, 25, P22, "failed", "cancel"),
        (22, 25, P22, "in-flight", None),        # a commit may be pending
        (22, 25, P22, "absent", None),           # the Job cannot be observed
        (22, 25, P22, "complete", None),         # complete but schema behind: inconsistent
        (22, 25, P22, None, None),               # job state not supplied
        (22, 25, P2425, "not-created", None),    # prior cannot be what serves 22
        (5, 6, None, "not-created", None),       # prior range unknown
        (25, 25, P22, "complete", "forward-fix-or-restore"),
        (25, 25, P2425, "complete", "image-revert"),
        (25, 25, P2425, "in-flight", None),
        (26, 25, P22, "failed", "forward-fix-or-restore"),
        (None, 25, P2425, "complete", None),
        (25, 25, None, "complete", None),
    ],
)
def test_recovery_class_is_computed_from_ranges(current, target, prior, job, expected):
    assert recovery_class(current, target, prior, job)[0] == expected


def test_preflight_before_an_incompatible_migration_is_cancel_then_forward_fix():
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25,
                                 current_schema=22, job_state="not-created",
                                 image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: (22, 22)}))
    body = result.result
    assert body["recovery_class"] == "cancel"
    assert body["recovery_class_after_commit"] == "forward-fix-or-restore"
    assert body["gates"] == {"candidate_admits_target": True, "prior_admits_current": True,
                             "prior_admits_target": False, "target_not_behind_current": True,
                             "migration_job_settled": True}
    assert result.status == "attention"
    assert {"kind": "candidate-image", "id": CANDIDATE} in result.work_refs


def test_preflight_with_an_additive_migration_allows_image_revert():
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25, current_schema=25,
                                 job_state="complete",
                                 image_runner=runner_for({CANDIDATE: (24, 25), PRIOR: (24, 25)}))
    assert result.result["recovery_class"] == "image-revert" and result.status == "ok"


def test_committed_schema_outside_prior_is_forward_fix_not_blocked():
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25, current_schema=25,
                                 job_state="complete", image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: (22, 22)}))
    assert result.result["blocked_by"] == [] and result.result["recovery_class"] == "forward-fix-or-restore"
    assert result.status == "attention"


def test_prior_that_cannot_serve_the_current_schema_blocks_instead_of_cancel():
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25, current_schema=22,
                                 job_state="not-created",
                                 image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: (24, 25)}))
    assert result.result["gates"]["prior_admits_current"] is False
    assert "prior_admits_current" in result.result["blocked_by"]
    assert result.result["recovery_class"] is None and result.status == "error"


@pytest.mark.parametrize("job", ["in-flight", "absent", None])
def test_unsettled_or_unobserved_job_yields_no_class(job):
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25, current_schema=22,
                                 job_state=job, image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: (22, 22)}))
    assert result.result["recovery_class"] is None and result.status == "error"


def test_preflight_blocks_a_candidate_that_does_not_admit_the_target():
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=26, current_schema=25,
                                 job_state="not-created", image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: (25, 25)}))
    assert result.result["blocked_by"] == ["candidate_admits_target"] and result.status == "error"


def test_current_schema_is_deduced_from_a_compatible_served_single_version_image():
    handshake = {"compatibility": {"domains": {"work": {"state": "compatible"}}}}
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25,
                                 served_handshake=handshake, served_image=CANDIDATE, job_state="complete",
                                 image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: (22, 22)}))
    assert result.result["current_schema"] == 25
    assert result.result["recovery_class"] == "forward-fix-or-restore"
    ranged = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25,
                                 served_handshake=handshake, served_image=CANDIDATE, job_state="complete",
                                 image_runner=runner_for({CANDIDATE: (24, 25), PRIOR: (22, 22)}))
    assert ranged.result["current_schema"] is None and ranged.result["recovery_class"] is None
    assert "current-work-schema-unknown" in ranged.unknowns and ranged.status == "error"
    # Without --served-image the prior image's range is never borrowed.
    unserved = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25,
                                   served_handshake=handshake, job_state="complete",
                                   image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: (25, 25)}))
    assert unserved.result["current_schema"] is None and unserved.status == "error"


def test_unreadable_image_is_an_unknown_not_a_guess():
    result = migration_preflight(candidate_image=CANDIDATE, prior_image=PRIOR, target_schema=25, current_schema=25,
                                 job_state="complete", image_runner=runner_for({CANDIDATE: (25, 25), PRIOR: None}))
    assert "prior-image-unreadable" in result.unknowns
    assert result.result["recovery_class"] is None and result.status == "error"


def test_historical_startup_proof_is_wrapped_only_where_it_applies(tmp_path):
    assert startup_proof(CANDIDATE, 25, tmp_path / "x.py")["state"] == "not-applicable"
    assert startup_proof(CANDIDATE, 6, tmp_path / "missing.py")["state"] == "unavailable"


def test_recovery_export_is_stamped_and_never_begins_a_namespace(tmp_path):
    from vuoro_client.recovery import RecoveryLog

    missing = recovery_export("incident-a", root=tmp_path / "root")
    assert missing.status == "error" and not (tmp_path / "root").exists()

    log = RecoveryLog(tmp_path / "root", "incident-a").begin()
    log.append(record_kind="observation", summary="api down", created_at="2026-10-14T00:00:00Z", detail={"probe": "503"}, basis_revision="rev-1")
    log.append(record_kind="requested-command", summary="restart", created_at="2026-10-14T00:01:00Z",
               requested_command={"argv": ["kubectl", "rollout", "restart"]})
    result = recovery_export("incident-a", root=tmp_path / "root", work_refs=[{"kind": "work-item", "id": "9"}])
    document = result.document(manifest_path=PINS, attestation_path=tmp_path / "none.json")
    assert document["result"]["record_count"] == 2
    assert document["result"]["record_kinds"] == {"observation": 1, "requested-command": 1}
    assert document["result"]["requested_commands_pending_review"] == 1 and document["status"] == "attention"
    assert {"kind": "incident", "id": "incident-a"} in document["work_refs"]
    assert {"kind": "basis-revision", "id": "rev-1"} in document["work_refs"]
    assert len(document["result"]["records_sha256"]) == 64


def test_cli_prints_one_document_and_maps_status_to_exit(tmp_path, capsys):
    out = tmp_path / "result.json"
    code = cli.main(["recovery-export", "--incident-id", "nope", "--root", str(tmp_path / "r"),
                     "--pins", str(PINS), "--attestation", str(tmp_path / "a.json"),
                     "--work-ref", "work-item:2600", "--output", str(out)])
    printed = capsys.readouterr().out
    assert code == 2 and out.read_text() == printed
    assert '"work-item"' in printed and '"recovery-export"' in printed
