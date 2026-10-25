"""Portable binding regression tests reconstructed after workspace loss.

The implementation was restored byte-for-byte from its remote Git blobs. This
suite freshly verifies the public contract; it is not a byte-identical recovery
of the earlier test file.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from vuoro_evidence.run import ObservedProfile, RunManifest, RunManifestError
from vuoro_evidence.workflow import (
    ProviderWorkflowBinding, RevisionRef, WorkReleaseRef, WorkflowBinding,
    WorkflowBindingError, WorkflowBindingMismatch, WorkflowBindingObservation,
    bind_workflow, main,
)


def content_digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def revision(name="requirements"):
    return RevisionRef("sprintctl", name, "r1", "sha256:" + "a" * 64)


def work_release():
    return WorkReleaseRef("sprintctl", "bayleafwalker/project", 42, "b" * 64)


def seal(document):
    document = deepcopy(document)
    document["workflow_revision"] = content_digest(document["definition"])
    document["binding_digest"] = content_digest(
        {key: value for key, value in document.items() if key != "binding_digest"}
    )
    return document


def provider_document():
    definition = {
        "apiVersion": "sovite.dev/v1alpha1",
        "kind": "DeliveryWorkflow",
        "id": "delivery",
        "mode": "direct",
        "provider_owned_intention": "Requirements drive the implementation — not a task list.",
        "domain_obligations": {"lineage": "capture"},
    }
    return seal({
        "apiVersion": "sovite.dev/v1alpha1",
        "kind": "DeliveryWorkflowBinding",
        "workflow_revision": content_digest(definition),
        "definition": definition,
        "installation_id": "installation-a",
        "bundle_generation": "sha256:" + "c" * 64,
        "input_class": "confidential",
        "roles": {"implementer": {
            "grant_id": "implementation", "agent_id": "developer",
            "harness_id": "local-agent", "harness_kind": "agent-http",
            "model_id": "installed-model", "model_adapter": "ollama",
            "model_destination": "private-model", "compute_id": "local",
            "compute_destination": "private-compute", "input_class": "confidential",
            "output_class": "confidential", "model_boundary": "private",
            "requires_output_release": False,
        }},
        "transfers": {},
        "requirements_ref": revision().as_dict(),
        "session_policy_ref": revision("session-policy").as_dict(),
        "dispatch_authorized": False,
    })


def mixed_document():
    document = provider_document()
    document["definition"]["mode"] = "coordinated"
    document["roles"]["inline_reviewer"] = {
        **document["roles"]["implementer"], "grant_id": "public-review",
        "agent_id": "reviewer", "model_adapter": "codex-subscription",
        "model_destination": "public-model", "input_class": "public",
        "output_class": "public", "model_boundary": "public",
        "requires_output_release": True,
    }
    document["transfers"] = {
        "review": {
            "from_role": "implementer", "target": {"kind": "role", "role": "inline_reviewer"},
            "contract_id": "approved-review-input", "contract_revision": "sha256:" + "d" * 64,
            "requires_output_release": True,
        },
        "finding": {
            "from_role": "inline_reviewer",
            "target": {"kind": "vuoro-finding", "destination": "vuoro"},
            "contract_id": None, "contract_revision": None,
            "requires_output_release": False,
        },
        "candidate": {
            "from_role": "inline_reviewer",
            "target": {"kind": "repository-candidate", "destination": "forgejo"},
            "contract_id": None, "contract_revision": None,
            "requires_output_release": False,
        },
    }
    return seal(document)


def expectations(provider):
    return {
        "work_release": work_release(), "requirements_ref": revision(),
        "session_policy_ref": revision("session-policy"),
        "workflow_revision": provider.workflow_revision,
        "installation_id": "installation-a",
        "bundle_generation": "sha256:" + "c" * 64,
        "input_class": "confidential",
    }


def bound(document=None):
    provider = ProviderWorkflowBinding.from_dict(document or provider_document())
    return bind_workflow(provider, **expectations(provider))


def test_reference_roundtrip_and_native_work_release_identity():
    ref = revision()
    assert RevisionRef.from_dict(ref.as_dict()) == ref
    release = work_release()
    assert WorkReleaseRef.from_dict(release.as_dict()) == release
    assert release.release_digest == "b" * 64
    with pytest.raises(FrozenInstanceError):
        ref.revision = "r2"


@pytest.mark.parametrize("field", ["owner", "resource_id", "revision"])
@pytest.mark.parametrize("value", ["", " surrounding ", "line\nbreak", None])
def test_revision_identity_fields_require_nonempty_bounded_text(field, value):
    payload = revision().as_dict()
    payload[field] = value
    with pytest.raises(WorkflowBindingError):
        RevisionRef.from_dict(payload)


@pytest.mark.parametrize("value", ["a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63,
                                   "sha256:" + "a" * 64 + "\n", None])
def test_reference_digest_has_exact_canonical_spelling(value):
    with pytest.raises(WorkflowBindingError):
        replace(revision(), content_digest=value)


@pytest.mark.parametrize("field", ["owner", "resource_id", "revision", "content_digest"])
def test_revision_ref_rejects_missing_fields(field):
    payload = revision().as_dict()
    del payload[field]
    with pytest.raises(WorkflowBindingError):
        RevisionRef.from_dict(payload)


def test_reference_and_work_identity_are_closed_envelopes():
    for cls, payload in ((RevisionRef, revision().as_dict()), (WorkReleaseRef, work_release().as_dict())):
        with pytest.raises(WorkflowBindingError):
            cls.from_dict({**payload, "authority": "granted"})


@pytest.mark.parametrize("item", [True, False, 0, -1, 1.5, "42"])
def test_work_release_item_is_a_positive_integer_not_boolean(item):
    with pytest.raises(WorkflowBindingError):
        replace(work_release(), item_id=item)


@pytest.mark.parametrize("value", ["sha256:" + "b" * 64, "B" * 64, "b" * 63, "b" * 64 + "\n"])
def test_work_release_preserves_bare_lowercase_owner_digest(value):
    with pytest.raises(WorkflowBindingError):
        replace(work_release(), release_digest=value)


@pytest.mark.parametrize("factory", [provider_document, mixed_document])
def test_provider_snapshot_roundtrips_opaque_definition_and_routes(factory):
    document = factory()
    provider = ProviderWorkflowBinding.from_dict(document)
    assert provider.as_dict() == document
    assert provider.binding_digest == document["binding_digest"]
    assert provider.requirements_ref == revision()
    assert provider.session_policy_ref == revision("session-policy")
    assert provider.input_class == "confidential"
    assert not provider.as_dict()["dispatch_authorized"]


@pytest.mark.parametrize("field", list(provider_document()))
def test_provider_envelope_requires_every_bound_identity(field):
    document = provider_document()
    del document[field]
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(document)


@pytest.mark.parametrize("value", [True, 0, 1, None, "false"])
def test_configuration_cannot_assert_or_coerce_dispatch_authority(value):
    document = provider_document()
    document["dispatch_authorized"] = value
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(seal(document))


@pytest.mark.parametrize("field,value", [
    ("apiVersion", "sovite.dev/v2"), ("kind", "DeliveryWorkflow"),
    ("installation_id", ""), ("input_class", ""), ("definition", {}),
    ("definition", []), ("roles", {}), ("transfers", []),
])
def test_provider_binding_refuses_malformed_envelopes(field, value):
    document = provider_document()
    document[field] = value
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(seal(document))


def test_unknown_provider_field_is_not_silently_dropped():
    document = provider_document()
    document["execution_succeeded"] = True
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(seal(document))


@pytest.mark.parametrize("field", list(provider_document()["roles"]["implementer"]))
def test_resolved_roles_require_complete_provider_identity(field):
    document = provider_document()
    del document["roles"]["implementer"][field]
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(seal(document))


@pytest.mark.parametrize("field,value", [
    ("grant_id", ""), ("model_boundary", "internal"), ("model_boundary", []),
    ("requires_output_release", 0), ("requires_output_release", "false"),
])
def test_role_classification_and_release_fields_are_strict(field, value):
    document = provider_document()
    document["roles"]["implementer"][field] = value
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(seal(document))


@pytest.mark.parametrize("value", [[], {}, None, 1, "unknown-target"])
def test_malformed_transfer_target_kind_has_bounded_validation_error(value):
    document = mixed_document()
    document["transfers"]["review"]["target"]["kind"] = value
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(seal(document))


@pytest.mark.parametrize("field,value", [
    ("contract_id", None), ("contract_revision", None),
    ("contract_revision", "sha256:short"), ("requires_output_release", 1),
    ("target", {"kind": "role", "destination": "wrong-field"}),
    ("target", {"kind": "repository-candidate", "role": "wrong-field"}),
])
def test_transfer_contract_pair_and_target_shapes_are_enforced(field, value):
    document = mixed_document()
    document["transfers"]["review"][field] = value
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(seal(document))


@pytest.mark.parametrize("alter", ["definition", "grant", "digest"])
def test_supplied_hashes_detect_changed_provider_bytes(alter):
    document = provider_document()
    if alter == "definition":
        document["definition"]["provider_owned_intention"] = "changed"
    elif alter == "grant":
        document["roles"]["implementer"]["grant_id"] = "changed"
    else:
        document["binding_digest"] = "sha256:" + "0" * 64
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(document)


def test_snapshots_are_detached_immutable_and_canonical():
    document = mixed_document()
    provider = ProviderWorkflowBinding.from_dict(document)
    original = deepcopy(document)
    document["definition"]["domain_obligations"]["lineage"] = "omit"
    exported = provider.as_dict()
    exported["roles"]["implementer"]["model_id"] = "different"
    assert provider.as_dict() == original
    reordered = {key: value for key, value in reversed(list(original.items()))}
    assert ProviderWorkflowBinding.from_dict(reordered) == provider
    with pytest.raises(FrozenInstanceError):
        provider._document = b"{}"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "\ud800", {1: "not-a-string-key"}])
def test_snapshot_rejects_non_json_or_non_utf8_definition_values(bad):
    document = provider_document()
    document["definition"]["invalid"] = bad
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding.from_dict(document)


@pytest.mark.parametrize("raw", [b"[]", b"{", b"\xff", b'{"kind":"one","kind":"two"}',
                                 b'{"extra":NaN}'])
def test_raw_json_input_fails_closed(raw):
    with pytest.raises(WorkflowBindingError):
        ProviderWorkflowBinding(raw)


def test_provider_definition_semantics_remain_provider_owned():
    document = provider_document()
    # Vuoro preserves provider data without becoming a second workflow compiler.
    document["definition"] = {"future_provider_extension": {"domain": ["lineage", "sandbox"]}}
    record = bound(seal(document))
    assert record.provider_binding.as_dict()["definition"] == document["definition"]


def test_work_join_roundtrip_covers_complete_owner_and_provider_identity():
    record = bound(mixed_document())
    exported = record.as_dict()
    assert WorkflowBinding.from_dict(exported) == record
    assert exported["source_authentication"] == "not_performed"
    assert exported["dispatch_authorized"] is False
    assert record.binding_digest == content_digest({k: v for k, v in exported.items() if k != "binding_digest"})
    assert replace(record, work_release=replace(record.work_release, item_id=43)).binding_digest != record.binding_digest
    exported["work_release"]["item_id"] = 100
    assert record.work_release.item_id == 42
    with pytest.raises(WorkflowBindingError):
        WorkflowBinding.from_dict(exported)


@pytest.mark.parametrize("field,value", [
    ("schema_version", "other"), ("source_authentication", "verified"),
    ("dispatch_authorized", True), ("binding_digest", "sha256:" + "0" * 64),
])
def test_outer_record_cannot_claim_authority_or_change_digest(field, value):
    exported = bound().as_dict()
    exported[field] = value
    with pytest.raises(WorkflowBindingError):
        WorkflowBinding.from_dict(exported)


@pytest.mark.parametrize("field", [
    "requirements_ref", "session_policy_ref", "workflow_revision", "installation_id",
    "bundle_generation", "input_class", "provider_binding_digest",
])
def test_each_explicit_expectation_is_compared(field):
    provider = ProviderWorkflowBinding.from_dict(provider_document())
    expected = expectations(provider)
    if field in {"requirements_ref", "session_policy_ref"}:
        expected[field] = replace(expected[field], revision="different")
    elif field == "provider_binding_digest":
        expected["expected_provider_binding_digest"] = "sha256:" + "e" * 64
    elif field in {"workflow_revision", "bundle_generation"}:
        expected[field] = "sha256:" + "e" * 64
    else:
        expected[field] = "different"
    with pytest.raises(WorkflowBindingMismatch) as rejected:
        bind_workflow(provider, **expected)
    assert tuple(item.field for item in rejected.value.mismatches) == (field,)
    assert rejected.value.mismatches[0].expected != rejected.value.mismatches[0].actual


def test_mismatches_report_all_changes_without_first_failure_short_circuit():
    provider = ProviderWorkflowBinding.from_dict(provider_document())
    expected = expectations(provider)
    expected.update(installation_id="different", input_class="public")
    with pytest.raises(WorkflowBindingMismatch) as rejected:
        bind_workflow(provider, **expected)
    assert [item.field for item in rejected.value.mismatches] == ["installation_id", "input_class"]


def test_exact_provider_digest_detects_rebound_model_even_with_same_product_inputs():
    original = ProviderWorkflowBinding.from_dict(provider_document())
    document = provider_document()
    document["roles"]["implementer"]["model_id"] = "another-installed-model"
    replacement = ProviderWorkflowBinding.from_dict(seal(document))
    with pytest.raises(WorkflowBindingMismatch) as rejected:
        bind_workflow(replacement, **expectations(replacement),
                      expected_provider_binding_digest=original.binding_digest)
    assert rejected.value.mismatches[0].field == "provider_binding_digest"


def test_observation_requires_separate_receipt_and_preserves_legacy_manifest_digest():
    base = RunManifest("run", "harness", "build", "model", "recipe", ObservedProfile("seen"))
    old = base.composition()
    assert "workflow_observation" not in old
    payload = {key: value for key, value in old.items() if key != "run_id"}
    expected = "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert base.composition_digest() == expected
    observation = WorkflowBindingObservation(revision("binding"), revision("provider-receipt"))
    assert WorkflowBindingObservation.from_dict(observation.as_dict()) == observation
    observed = replace(base, workflow_observation=observation)
    assert observed.composition()["workflow_observation"] == observation.as_dict()
    assert observed.composition_digest() != base.composition_digest()
    with pytest.raises(WorkflowBindingError):
        WorkflowBindingObservation.from_dict({"binding_ref": revision().as_dict()})
    with pytest.raises(RunManifestError):
        replace(base, workflow_observation=bound())


def cli_inputs(tmp_path):
    document = provider_document()
    paths = {}
    for name, payload in {
        "provider-binding": document, "work-release": work_release().as_dict(),
        "requirements-ref": revision().as_dict(),
        "session-policy-ref": revision("session-policy").as_dict(),
    }.items():
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = str(path)
    args = ["bind"]
    for name, path in paths.items():
        args += ["--" + name, path]
    args += ["--workflow-revision", document["workflow_revision"],
             "--installation-id", document["installation_id"],
             "--bundle-generation", document["bundle_generation"],
             "--input-class", document["input_class"]]
    return args, paths


def test_cli_bind_verify_and_existing_output_refusal(tmp_path, capsys):
    args, paths = cli_inputs(tmp_path)
    output = tmp_path / "binding.json"
    assert main(args + ["--output", str(output)]) == 0
    saved = output.read_bytes()
    assert main(["verify", "--binding", str(output),
                 "--provider-binding", paths["provider-binding"]]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "structurally_consistent"
    assert checked["source_authentication"] == "not_performed"
    assert checked["dispatch_authorized"] is False
    assert main(args + ["--output", str(output)]) == 2
    assert output.read_bytes() == saved
    assert json.loads(capsys.readouterr().err)["error"] == "FileExistsError"


def test_cli_stdout_and_mismatch_failure(tmp_path, capsys):
    args, _ = cli_inputs(tmp_path)
    assert main(args) == 0
    assert WorkflowBinding.from_dict(json.loads(capsys.readouterr().out))
    args[-1] = "public"
    assert main(args) == 2
    failure = json.loads(capsys.readouterr().err)
    assert failure["mismatches"][0]["field"] == "input_class"


def test_cli_rejects_duplicate_json_fields(tmp_path, capsys):
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"one","schema_version":"two"}')
    assert main(["verify", "--binding", str(path)]) == 2
    assert "duplicate" in json.loads(capsys.readouterr().err)["message"]


def test_module_cli_has_no_circular_import_runtime_warning():
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    environment = {**os.environ, "PYTHONPATH": source_root}
    completed = subprocess.run([sys.executable, "-m", "vuoro_evidence.workflow", "--help"],
                               env=environment, capture_output=True, text=True)
    assert completed.returncode == 0
    assert "RuntimeWarning" not in completed.stderr
    assert "bind" in completed.stdout and "verify" in completed.stdout
