"""Immutable delivery bindings over provider-owned workflow definitions.

Sovite owns executable definitions, installation generations, grants and admission.
Sprintctl (or another work owner) owns the work release and acceptance. This module
only joins their supplied references, verifies structural/digest consistency and
compares the result with explicitly supplied expectations. It performs no network
access, source authentication, policy evaluation, dispatch or acceptance.

A provider binding is a selected configuration, not an observation that it ran.
``WorkflowBindingObservation`` separately identifies a provider receipt supplied by
the caller; this module does not authenticate or interpret that receipt either.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


SCHEMA_VERSION = "vuoro-workflow-binding/v1"
PROVIDER_API_VERSION = "sovite.dev/v1alpha1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RELEASE_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PROVIDER_FIELDS = frozenset({
    "apiVersion", "kind", "workflow_revision", "definition", "installation_id",
    "bundle_generation", "input_class", "roles", "transfers", "requirements_ref", "session_policy_ref",
    "dispatch_authorized", "binding_digest",
})
_ROLE_FIELDS = frozenset({
    "grant_id", "agent_id", "harness_id", "harness_kind", "model_id",
    "model_adapter", "model_destination", "compute_id", "compute_destination",
    "input_class", "output_class", "model_boundary", "requires_output_release",
})
_TRANSFER_FIELDS = frozenset({
    "from_role", "target", "contract_id", "contract_revision", "requires_output_release",
})


class WorkflowBindingError(ValueError):
    """A supplied record is malformed, incomplete or inconsistent."""


@dataclass(frozen=True)
class BindingMismatch:
    field: str
    expected: str
    actual: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "expected": self.expected, "actual": self.actual}


class WorkflowBindingMismatch(WorkflowBindingError):
    """A structurally valid provider record differs from the requested binding."""

    def __init__(self, mismatches: tuple[BindingMismatch, ...]) -> None:
        self.mismatches = mismatches
        super().__init__("workflow binding differs: " + ", ".join(m.field for m in mismatches))


def _text(name: str, value: Any) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise WorkflowBindingError(f"{name} must be nonempty text without surrounding whitespace or control characters")
    return value


def _digest_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise WorkflowBindingError(f"{name} must be sha256 followed by 64 lowercase hexadecimal digits")
    return value


def _boolean(name: str, value: Any) -> bool:
    if type(value) is not bool:
        raise WorkflowBindingError(f"{name} must be a boolean")
    return value


def _object(name: str, value: Any, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise WorkflowBindingError(f"{name} must contain exactly: {', '.join(sorted(fields))}")
    return value


def _canonical(value: Any) -> bytes:
    def check_keys(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise WorkflowBindingError("JSON object keys must be strings")
            for child in item.values():
                check_keys(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check_keys(child)

    check_keys(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise WorkflowBindingError("record must be finite UTF-8 JSON") from error


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowBindingError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise WorkflowBindingError(f"non-finite JSON value: {value}")


def _load_json(raw: bytes | str) -> Any:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw, object_pairs_hook=_unique_pairs,
                          parse_constant=_invalid_constant)
    except (ValueError, UnicodeDecodeError) as error:
        if isinstance(error, WorkflowBindingError):
            raise
        raise WorkflowBindingError("record is not valid UTF-8 JSON") from error


@dataclass(frozen=True)
class RevisionRef:
    """An owner-provided immutable revision reference, not an authority grant.

    The caller must obtain and authenticate the source. ``content_digest`` binds
    the referenced content; this record alone cannot resolve or verify its bytes.
    """

    owner: str
    resource_id: str
    revision: str
    content_digest: str

    def __post_init__(self) -> None:
        for name in ("owner", "resource_id", "revision"):
            _text(name, getattr(self, name))
        _digest_text("content_digest", self.content_digest)

    def as_dict(self) -> dict[str, str]:
        return {"owner": self.owner, "resource_id": self.resource_id,
                "revision": self.revision, "content_digest": self.content_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RevisionRef:
        return cls(**_object("revision reference", value, frozenset({
            "owner", "resource_id", "revision", "content_digest",
        })))


@dataclass(frozen=True)
class WorkReleaseRef:
    """A supplied work-owner release identity; no work lifecycle is recreated.

    ``release_digest`` retains the native bare-hex Sprintctl release format.
    The owner/repository/item tuple prevents identical digests in different work
    scopes from being substituted. Currentness and acceptance remain owner checks.
    """

    owner: str
    repository_id: str
    item_id: int
    release_digest: str

    def __post_init__(self) -> None:
        _text("owner", self.owner)
        _text("repository_id", self.repository_id)
        if type(self.item_id) is not int or self.item_id < 1:
            raise WorkflowBindingError("item_id must be a positive integer")
        if (not isinstance(self.release_digest, str)
                or not _RELEASE_DIGEST.fullmatch(self.release_digest)):
            raise WorkflowBindingError("release_digest must contain 64 lowercase hexadecimal digits")

    def as_dict(self) -> dict[str, Any]:
        return {"owner": self.owner, "repository_id": self.repository_id,
                "item_id": self.item_id, "release_digest": self.release_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WorkReleaseRef:
        return cls(**_object("work release reference", value, frozenset({
            "owner", "repository_id", "item_id", "release_digest",
        })))


@dataclass(frozen=True)
class ProviderWorkflowBinding:
    """A frozen, structurally checked snapshot of Sovite's supplied binding.

    The definition remains opaque: Vuoro never validates its graph, review
    semantics, domain obligations, grants or model/provider eligibility. The
    closed envelope is the explicit interoperability contract, not a compiler.
    Raw bytes are canonicalized to prevent caller mutation after validation.
    """

    _document: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._document, bytes):
            raise WorkflowBindingError("provider binding must be supplied as JSON bytes")
        value = _object("provider binding", _load_json(self._document), _PROVIDER_FIELDS)
        if value["apiVersion"] != PROVIDER_API_VERSION or value["kind"] != "DeliveryWorkflowBinding":
            raise WorkflowBindingError("unsupported provider workflow binding version or kind")
        if value["dispatch_authorized"] is not False:
            raise WorkflowBindingError("a workflow binding cannot assert dispatch authorization")
        _text("installation_id", value["installation_id"])
        _text("input_class", value["input_class"])
        for name in ("workflow_revision", "bundle_generation", "binding_digest"):
            _digest_text(name, value[name])
        RevisionRef.from_dict(value["requirements_ref"])
        RevisionRef.from_dict(value["session_policy_ref"])
        if not isinstance(value["definition"], dict) or not value["definition"]:
            raise WorkflowBindingError("provider definition must be a nonempty JSON object")
        if _digest(value["definition"]) != value["workflow_revision"]:
            raise WorkflowBindingError("workflow_revision does not match the supplied definition")
        if not isinstance(value["roles"], dict) or not value["roles"]:
            raise WorkflowBindingError("roles must contain supplied role bindings")
        for role, binding in value["roles"].items():
            _text("role", role)
            _object("role binding", binding, _ROLE_FIELDS)
            for name, field_value in binding.items():
                if name == "requires_output_release":
                    _boolean(f"roles.{role}.{name}", field_value)
                else:
                    _text(f"roles.{role}.{name}", field_value)
            if binding["model_boundary"] not in {"private", "public"}:
                raise WorkflowBindingError("model_boundary must be private or public")
        if not isinstance(value["transfers"], dict):
            raise WorkflowBindingError("transfers must be a JSON object")
        for transfer, binding in value["transfers"].items():
            _text("transfer", transfer)
            _object("transfer binding", binding, _TRANSFER_FIELDS)
            _text("from_role", binding["from_role"])
            _boolean("requires_output_release", binding["requires_output_release"])
            target = binding["target"]
            if not isinstance(target, dict):
                raise WorkflowBindingError("transfer target must be an object")
            target_kind = _text("target.kind", target.get("kind"))
            if target_kind == "role":
                _object("transfer role target", target, frozenset({"kind", "role"}))
                _text("target.role", target["role"])
            elif target_kind in {"repository-candidate", "vuoro-finding"}:
                _object("transfer destination target", target, frozenset({"kind", "destination"}))
                _text("target.destination", target["destination"])
            else:
                raise WorkflowBindingError("unsupported transfer target kind")
            if binding["contract_id"] is None and binding["contract_revision"] is None:
                continue
            _text("contract_id", binding["contract_id"])
            _digest_text("contract_revision", binding["contract_revision"])
        body = {k: v for k, v in value.items() if k != "binding_digest"}
        if _digest(body) != value["binding_digest"]:
            raise WorkflowBindingError("binding_digest does not match the supplied provider binding")
        object.__setattr__(self, "_document", _canonical(value))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderWorkflowBinding:
        return cls(_canonical(value))

    def as_dict(self) -> dict[str, Any]:
        """Return a detached export; mutating it cannot change this snapshot."""
        return _load_json(self._document)

    @property
    def binding_digest(self) -> str:
        return self.as_dict()["binding_digest"]

    @property
    def workflow_revision(self) -> str:
        return self.as_dict()["workflow_revision"]

    @property
    def installation_id(self) -> str:
        return self.as_dict()["installation_id"]

    @property
    def bundle_generation(self) -> str:
        return self.as_dict()["bundle_generation"]

    @property
    def input_class(self) -> str:
        return self.as_dict()["input_class"]

    @property
    def requirements_ref(self) -> RevisionRef:
        return RevisionRef.from_dict(self.as_dict()["requirements_ref"])

    @property
    def session_policy_ref(self) -> RevisionRef:
        return RevisionRef.from_dict(self.as_dict()["session_policy_ref"])


@dataclass(frozen=True)
class WorkflowBinding:
    """An immutable selected workflow/work-release join, never run acceptance."""

    work_release: WorkReleaseRef
    provider_binding: ProviderWorkflowBinding

    def __post_init__(self) -> None:
        if not isinstance(self.work_release, WorkReleaseRef):
            raise WorkflowBindingError("work_release must be a WorkReleaseRef")
        if not isinstance(self.provider_binding, ProviderWorkflowBinding):
            raise WorkflowBindingError("provider_binding must be a ProviderWorkflowBinding")

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "work_release": self.work_release.as_dict(),
            "provider_binding": self.provider_binding.as_dict(),
            "source_authentication": "not_performed",
            "dispatch_authorized": False,
        }

    @property
    def binding_digest(self) -> str:
        return _digest(self._body())

    def as_dict(self) -> dict[str, Any]:
        return {**self._body(), "binding_digest": self.binding_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WorkflowBinding:
        value = _object("workflow binding", value, frozenset({
            "schema_version", "work_release", "provider_binding",
            "source_authentication", "dispatch_authorized", "binding_digest",
        }))
        if (value["schema_version"] != SCHEMA_VERSION
                or value["source_authentication"] != "not_performed"
                or value["dispatch_authorized"] is not False):
            raise WorkflowBindingError("unsupported workflow binding schema or authority claim")
        result = cls(WorkReleaseRef.from_dict(value["work_release"]),
                     ProviderWorkflowBinding.from_dict(value["provider_binding"]))
        if value["binding_digest"] != result.binding_digest:
            raise WorkflowBindingError("workflow binding digest does not match its supplied records")
        return result


def bind_workflow(
    provider_binding: ProviderWorkflowBinding, *, work_release: WorkReleaseRef,
    requirements_ref: RevisionRef, session_policy_ref: RevisionRef,
    workflow_revision: str, installation_id: str, bundle_generation: str, input_class: str,
    expected_provider_binding_digest: str | None = None,
) -> WorkflowBinding:
    """Bind only a provider snapshot matching every explicit caller expectation.

    Expectations must come from the caller's authoritative inputs, not be copied
    from an untrusted candidate. Matching is structural and does not establish
    current provider admission, source authenticity or work-owner acceptance.
    """
    if not isinstance(provider_binding, ProviderWorkflowBinding):
        raise WorkflowBindingError("provider_binding must be a ProviderWorkflowBinding")
    if not isinstance(work_release, WorkReleaseRef):
        raise WorkflowBindingError("work_release must be a WorkReleaseRef")
    if not isinstance(requirements_ref, RevisionRef) or not isinstance(session_policy_ref, RevisionRef):
        raise WorkflowBindingError("expected requirements and session policy must be RevisionRefs")
    _digest_text("workflow_revision", workflow_revision)
    _text("installation_id", installation_id)
    _digest_text("bundle_generation", bundle_generation)
    _text("input_class", input_class)
    pairs = {
        "requirements_ref": (requirements_ref.as_dict(), provider_binding.requirements_ref.as_dict()),
        "session_policy_ref": (session_policy_ref.as_dict(), provider_binding.session_policy_ref.as_dict()),
        "workflow_revision": (workflow_revision, provider_binding.workflow_revision),
        "installation_id": (installation_id, provider_binding.installation_id),
        "bundle_generation": (bundle_generation, provider_binding.bundle_generation),
        "input_class": (input_class, provider_binding.input_class),
    }
    if expected_provider_binding_digest is not None:
        _digest_text("expected_provider_binding_digest", expected_provider_binding_digest)
        pairs["provider_binding_digest"] = (expected_provider_binding_digest, provider_binding.binding_digest)
    mismatches = tuple(BindingMismatch(
        name, _canonical(expected).decode(), _canonical(actual).decode(),
    ) for name, (expected, actual) in pairs.items() if expected != actual)
    if mismatches:
        raise WorkflowBindingMismatch(mismatches)
    return WorkflowBinding(work_release, provider_binding)


@dataclass(frozen=True)
class WorkflowBindingObservation:
    """References to a selected binding and a separately supplied owner receipt.

    Unlike a definition/binding alone, this explicitly identifies observation
    provenance. Receipt truth, identity, outcome and correlation are not inferred
    here; the caller must authenticate and inspect the referenced owner record.
    """

    binding_ref: RevisionRef
    provider_receipt_ref: RevisionRef

    def __post_init__(self) -> None:
        if not isinstance(self.binding_ref, RevisionRef) or not isinstance(self.provider_receipt_ref, RevisionRef):
            raise WorkflowBindingError("workflow observation requires binding and provider receipt RevisionRefs")

    def as_dict(self) -> dict[str, Any]:
        return {"binding_ref": self.binding_ref.as_dict(),
                "provider_receipt_ref": self.provider_receipt_ref.as_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WorkflowBindingObservation:
        value = _object("workflow observation", value, frozenset({
            "binding_ref", "provider_receipt_ref",
        }))
        return cls(RevisionRef.from_dict(value["binding_ref"]),
                   RevisionRef.from_dict(value["provider_receipt_ref"]))


def _read(path: str) -> Any:
    return _load_json(Path(path).read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bind = commands.add_parser("bind", help="bind supplied provider configuration to a work release")
    bind.add_argument("--provider-binding", required=True)
    bind.add_argument("--work-release", required=True)
    bind.add_argument("--requirements-ref", required=True)
    bind.add_argument("--session-policy-ref", required=True)
    bind.add_argument("--workflow-revision", required=True)
    bind.add_argument("--installation-id", required=True)
    bind.add_argument("--bundle-generation", required=True)
    bind.add_argument("--input-class", required=True)
    bind.add_argument("--expected-provider-binding-digest")
    bind.add_argument("--output", help="new output file; existing files are never replaced")
    verify = commands.add_parser("verify", help="check exported binding integrity without source or policy evaluation")
    verify.add_argument("--binding", required=True)
    verify.add_argument("--provider-binding", help="optional separately obtained provider snapshot to compare")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "bind":
            record = bind_workflow(
                ProviderWorkflowBinding.from_dict(_read(arguments.provider_binding)),
                work_release=WorkReleaseRef.from_dict(_read(arguments.work_release)),
                requirements_ref=RevisionRef.from_dict(_read(arguments.requirements_ref)),
                session_policy_ref=RevisionRef.from_dict(_read(arguments.session_policy_ref)),
                workflow_revision=arguments.workflow_revision,
                installation_id=arguments.installation_id,
                bundle_generation=arguments.bundle_generation,
                input_class=arguments.input_class,
                expected_provider_binding_digest=arguments.expected_provider_binding_digest,
            )
            payload = json.dumps(record.as_dict(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            if arguments.output:
                with Path(arguments.output).open("x", encoding="utf-8") as output:
                    output.write(payload)
            else:
                sys.stdout.write(payload)
        else:
            record = WorkflowBinding.from_dict(_read(arguments.binding))
            if arguments.provider_binding:
                provider = ProviderWorkflowBinding.from_dict(_read(arguments.provider_binding))
                if provider.binding_digest != record.provider_binding.binding_digest:
                    raise WorkflowBindingMismatch((BindingMismatch(
                        "provider_binding_digest", record.provider_binding.binding_digest,
                        provider.binding_digest,
                    ),))
            print(json.dumps({
                "schema_version": "vuoro-workflow-binding-check/v1",
                "status": "structurally_consistent", "binding_digest": record.binding_digest,
                "source_authentication": "not_performed", "dispatch_authorized": False,
            }, sort_keys=True))
        return 0
    except (WorkflowBindingError, OSError) as error:
        result: dict[str, Any] = {"error": type(error).__name__, "message": str(error)}
        if isinstance(error, WorkflowBindingMismatch):
            result["mismatches"] = [item.as_dict() for item in error.mismatches]
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
