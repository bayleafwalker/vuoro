"""Read-only probes of a served Vuoro: handshake, catalog and identity.

Only ``GET`` handshake/catalog requests and ``read``-semantics invocations
with no idempotency key are ever sent.  Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
from typing import Any
import urllib.error
import urllib.request
import uuid

from vuoro_service import __version__

from .result import DiagnosticResult, composition_pins_sha256, load_pins
from .runtime import ImageRunner, ProbeError, probe_source, tls_context

CLIENT_PROTOCOL = 1
PROFILE_SCHEMA_VERSION = "vuoro-client-profile/v1"
USER_AGENT = f"vuoro-ops/{__version__}"

# (method, path, headers, json body) -> (status, json body or None)
Transport = Callable[[str, str, Mapping[str, str], Any], tuple[int, Any]]


class ProbeRefused(ValueError):
    """The probe was asked to do something it must not do."""


@dataclass(frozen=True)
class ServedProfile:
    profile_id: str
    endpoint: str
    credential_ref: str
    expected_environment: str
    environment_class: str
    required_authorities: tuple[str, ...] = field(default_factory=tuple)


def load_served_profile(path: Path) -> ServedProfile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ProbeRefused(f"profile {path} is not {PROFILE_SCHEMA_VERSION}")
    target = raw.get("target") or {}
    if target.get("environment_class") == "production" and raw.get("production_endpoint_denied"):
        raise ProbeRefused("profile denies its production endpoint")
    endpoint = target.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ProbeRefused("profile endpoint must be https (or loopback http)")
    return ServedProfile(
        profile_id=raw["id"],
        endpoint=endpoint.rstrip("/"),
        credential_ref=raw["credential_ref"],
        expected_environment=target["environment_id"],
        environment_class=target["environment_class"],
        required_authorities=tuple(raw.get("required_authorities") or ()),
    )


def resolve_file_credential(ref: str) -> str:
    """Resolve ``file:`` references with the owner's file-credential rules."""
    if not ref.startswith("file:"):
        raise ProbeRefused("unsupported credential reference scheme; expected 'file:'")
    raw_path = ref[len("file:"):]
    if raw_path.startswith("~/"):
        path = Path.home() / raw_path[2:]
    elif raw_path.startswith("/"):
        path = Path(raw_path)
    else:
        raise ProbeRefused("credential file reference must be absolute or start with '~/'")
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProbeRefused("credential file must be a regular, non-symlink file")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ProbeRefused("credential file must be owned by the current user with mode 0600")
    token = path.read_bytes().removesuffix(b"\n").decode("utf-8")
    if not token:
        raise ProbeRefused("credential file is empty")
    return token


def urllib_transport(endpoint: str, *, timeout: float = 20.0) -> Transport:
    def send(method: str, path: str, headers: Mapping[str, str], body: Any) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            endpoint + path,
            data=data,
            method=method,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                     **({"Content-Type": "application/json"} if data else {}), **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=tls_context()) as response:  # noqa: S310 - profile https
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
        try:
            return status, json.loads(payload) if payload else None
        except json.JSONDecodeError:
            return status, {"non_json_body": payload[:200].decode("utf-8", "replace")}

    return send


class ServedReader:
    """Handshake, catalog and read-only invocation against one profile."""

    def __init__(self, profile: ServedProfile, transport: Transport, token: str | None):
        self.profile = profile
        self._transport = transport
        self._token = token
        self.catalog_document: dict[str, Any] | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"X-Vuoro-Client-Protocol": str(CLIENT_PROTOCOL)}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def handshake(self) -> tuple[int, Any]:
        return self._transport("GET", "/api/meta/v1/handshake", self._headers(), None)

    def catalog(self) -> tuple[int, Any]:
        status, body = self._transport("GET", "/api/catalog/v1", self._headers(), None)
        if status == 200 and isinstance(body, dict):
            self.catalog_document = body
        return status, body

    def operation(self, name: str) -> dict[str, Any] | None:
        if self.catalog_document is None:
            self.catalog()
        for operation in (self.catalog_document or {}).get("operations", []):
            if operation.get("name") == name:
                return operation
        return None

    def read(self, name: str, arguments: dict[str, Any], *, repo_id: str | None) -> tuple[int, Any]:
        """Invoke one operation whose catalog semantics are ``read``; refuse anything else."""
        operation = self.operation(name)
        if operation is None:
            return 404, {"error": {"code": "operation-not-in-catalog", "message": name}}
        if operation.get("execution_semantics") != "read":
            raise ProbeRefused(f"{name} is not a read operation; diagnostics never write")
        payload = {
            "schema_version": "invocation/v1",
            "request_id": f"vuoro-ops-{uuid.uuid4()}",
            "operation": name,
            "arguments": arguments,
            "catalog_revision": self.catalog_document["revision"],
            "basis_revision": None,
            "idempotency_key": None,
            "repo_id": repo_id,
        }
        return self._transport("POST", "/api/invoke/v1", self._headers(), payload)


def _mismatch(kind: str, expected: Any, observed: Any, severity: str = "attention") -> dict[str, Any]:
    return {"kind": kind, "expected": expected, "observed": observed, "severity": severity}


def _handshake_checks(
    profile: ServedProfile, handshake: dict[str, Any], pins: dict[str, Any], local_version: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    environment = handshake.get("environment") or {}
    if environment.get("name") != profile.expected_environment:
        mismatches.append(_mismatch("environment", profile.expected_environment, environment.get("name"), "error"))
    if environment.get("environment_class") != profile.environment_class:
        mismatches.append(_mismatch("environment-class", profile.environment_class, environment.get("environment_class"), "error"))
    protocol = handshake.get("client_protocol") or {}
    if not (protocol.get("minimum", 0) <= CLIENT_PROTOCOL <= protocol.get("maximum", -1)):
        mismatches.append(_mismatch("client-protocol", CLIENT_PROTOCOL, protocol, "error"))
    compatibility = handshake.get("compatibility") or {}
    if compatibility.get("state") != "compatible":
        mismatches.append(_mismatch("compatibility", "compatible", compatibility.get("state"), "error"))
    served_domains = compatibility.get("domains") or {}
    for domain, state in sorted(served_domains.items()):
        if (state or {}).get("state") != "compatible":
            mismatches.append(_mismatch(f"domain-compatibility:{domain}", "compatible", state))
    if handshake.get("service_version") != local_version:
        mismatches.append(_mismatch("service-version", local_version, handshake.get("service_version")))
    pinned_domains = set()
    for descriptor in pins.get("runtime_descriptors", []):
        domain = descriptor.get("domain")
        pinned_domains.add(domain)
        served = served_domains.get(domain)
        if served is None:
            mismatches.append(_mismatch(f"pinned-domain-not-served:{domain}", "served", None, "error"))
            continue
        # The served domain schema_version is the owner's own compatibility
        # record label (Sprintctl reports its handshake document schema), so
        # only the API contract is comparable with the pin.
        if served.get("api_version") != descriptor.get("api_version"):
            mismatches.append(_mismatch(f"api-version:{domain}", descriptor.get("api_version"), served.get("api_version")))
    for domain in sorted(set(served_domains) - pinned_domains):
        mismatches.append(_mismatch("unpinned-served-domain", None, domain))
    summary = {
        "environment": environment.get("name"),
        "environment_class": environment.get("environment_class"),
        "service_version": handshake.get("service_version"),
        "catalog_revision": handshake.get("catalog_revision"),
        "compatibility": compatibility.get("state"),
        "domains": {
            d: {k: (s or {}).get(k) for k in ("state", "api_version", "schema_version", "reason")}
            for d, s in sorted(served_domains.items())
        },
        "api_versions": handshake.get("api_versions"),
        "schema_versions": handshake.get("schema_versions"),
        "client_protocol": protocol,
    }
    return mismatches, summary


def _status(mismatches: list[dict[str, Any]], unknowns: list[str]) -> str:
    if any(item["severity"] == "error" for item in mismatches):
        return "error"
    return "attention" if mismatches else "ok"


def _reader(profile_path: Path, transport: Transport | None, token: str | None) -> ServedReader:
    profile = load_served_profile(profile_path)
    if token is None:
        token = resolve_file_credential(profile.credential_ref)
    return ServedReader(profile, transport or urllib_transport(profile.endpoint), token)


def role_probe(
    profile_path: Path,
    *,
    manifest_path: Path,
    repo_id: str | None,
    transport: Transport | None = None,
    token: str | None = None,
    local_version: str = __version__,
) -> DiagnosticResult:
    reader = _reader(profile_path, transport, token)
    profile = reader.profile
    pins = load_pins(manifest_path)
    unknowns: list[str] = []
    status_code, handshake = reader.handshake()
    if status_code != 200 or not isinstance(handshake, dict):
        return DiagnosticResult("role-probe", "1", "error", {
            "endpoint": profile.endpoint, "handshake_status": status_code, "handshake": handshake,
        }, unknowns=["served-handshake-unavailable"])
    mismatches, summary = _handshake_checks(profile, handshake, pins, local_version)
    catalog_status, catalog = reader.catalog()
    if catalog_status != 200:
        mismatches.append(_mismatch("catalog-available", 200, catalog_status, "error"))
    identity_status, identity = reader.read("work.identity.current", {}, repo_id=repo_id)
    identity_summary: dict[str, Any] = {"status": identity_status}
    if identity_status == 200 and isinstance(identity, dict) and identity.get("status") == "accepted":
        identity_summary.update(identity.get("result") or {})
        if repo_id is not None and identity_summary.get("repo_id") != repo_id:
            mismatches.append(_mismatch("identity-repo", repo_id, identity_summary.get("repo_id"), "error"))
    else:
        identity_summary["error"] = (identity or {}).get("error") if isinstance(identity, dict) else identity
        mismatches.append(_mismatch("identity-current", "accepted", identity_summary["error"], "error"))

    authorities: dict[str, dict[str, Any]] = {}
    for authority in profile.required_authorities:
        candidates = [
            operation for operation in (reader.catalog_document or {}).get("operations", [])
            if operation.get("required_authority") == authority
            and operation.get("execution_semantics") == "read"
            and not (operation.get("input_schema") or {}).get("required")
            and operation.get("idempotency") != "required"
        ]
        candidates.sort(key=lambda op: (op["name"] != "work.identity.current", op["name"]))
        if not candidates:
            authorities[authority] = {"state": "not-observable-read-only"}
            unknowns.append(f"authority-not-observable-read-only:{authority}")
            continue
        probe = candidates[0]["name"]
        if probe == "work.identity.current":
            code, body = identity_status, identity
        else:
            code, body = reader.read(probe, {}, repo_id=repo_id)
        granted = code == 200 and isinstance(body, dict) and body.get("status") == "accepted"
        error_code = None if granted else ((body or {}).get("error") or {}).get("code") if isinstance(body, dict) else None
        denied = code in (401, 403) or (error_code or "").startswith(("forbidden", "unauthor", "authority"))
        authorities[authority] = {
            "probe_operation": probe,
            "state": "granted" if granted else "denied" if denied else "inconclusive",
            "status": code,
            **({"error_code": error_code} if error_code else {}),
        }
        if denied:
            mismatches.append(_mismatch(f"authority:{authority}", "granted", "denied", "error"))
        elif not granted:
            unknowns.append(f"authority-probe-inconclusive:{authority}")

    result = {
        "endpoint": profile.endpoint,
        "profile": profile.profile_id,
        "handshake": summary,
        "identity": identity_summary,
        "required_authorities": authorities,
        "mismatches": mismatches,
    }
    return DiagnosticResult("role-probe", "1", _status(mismatches, unknowns), result,
                            work_refs=[{"kind": "environment", "id": profile.expected_environment}],
                            unknowns=unknowns)


def catalog_probe(
    profile_path: Path,
    *,
    manifest_path: Path,
    transport: Transport | None = None,
    token: str | None = None,
    expected: dict[str, Any] | None = None,
    expected_from_image: str | None = None,
    image_runner: ImageRunner | None = None,
    local_version: str = __version__,
) -> DiagnosticResult:
    reader = _reader(profile_path, transport, token)
    profile = reader.profile
    pins = load_pins(manifest_path)
    unknowns: list[str] = []
    status_code, handshake = reader.handshake()
    if status_code != 200 or not isinstance(handshake, dict):
        return DiagnosticResult("catalog-probe", "1", "error", {
            "endpoint": profile.endpoint, "handshake_status": status_code,
        }, unknowns=["served-handshake-unavailable"])
    mismatches, summary = _handshake_checks(profile, handshake, pins, local_version)
    catalog_status, catalog = reader.catalog()
    if catalog_status != 200 or not isinstance(catalog, dict):
        mismatches.append(_mismatch("catalog-available", 200, catalog_status, "error"))
        return DiagnosticResult("catalog-probe", "1", "error",
                                {"endpoint": profile.endpoint, "handshake": summary, "mismatches": mismatches},
                                unknowns=unknowns)
    operations = catalog.get("operations", [])
    counts: dict[str, int] = {}
    for operation in operations:
        counts[operation.get("owning_domain")] = counts.get(operation.get("owning_domain"), 0) + 1
    observed = {
        "schema_version": catalog.get("schema_version"),
        "revision": catalog.get("revision"),
        "total": len(operations),
        "domain_counts": dict(sorted(counts.items())),
        "semantics_counts": _count(operations, "execution_semantics"),
    }
    if catalog.get("revision") != handshake.get("catalog_revision"):
        mismatches.append(_mismatch("catalog-revision-vs-handshake", handshake.get("catalog_revision"), catalog.get("revision"), "error"))
    pinned_domains = {item.get("domain") for item in pins.get("runtime_descriptors", [])}
    unpinned = sorted(set(counts) - pinned_domains)
    if unpinned:
        mismatches.append(_mismatch("operations-from-unpinned-domain", sorted(pinned_domains), unpinned))

    expected_source = None
    expected_names: list[str] | None = None
    if expected:
        expected_source = "operator-supplied"
    elif expected_from_image:
        runner = image_runner
        if runner is None:
            from .runtime import run_in_image as runner
        try:
            image = runner(expected_from_image, probe_source("image_composition"),
                           {"VUORO_OPS_DERIVE_CATALOG": "1"})
            derived = image.get("catalog") or {}
            if "error" in derived or not derived:
                unknowns.append("expected-catalog-not-derivable-from-image")
            else:
                expected = {k: derived[k] for k in ("revision", "total", "domain_counts")}
                expected_names = derived.get("operations")
                expected_source = f"derived:{expected_from_image}"
                observed["image_manifest_sha256"] = image.get("manifest_sha256")
                local_pins = composition_pins_sha256(manifest_path)
                if image.get("manifest_sha256") != local_pins:
                    mismatches.append(_mismatch("image-manifest-vs-pins", local_pins, image.get("manifest_sha256")))
        except ProbeError as error:
            unknowns.append("expected-catalog-image-probe-failed")
            observed["image_probe_error"] = str(error)[:300]
    else:
        unknowns.append("expected-catalog-not-supplied")
    if expected:
        for key in ("revision", "total", "domain_counts"):
            if key in expected and expected[key] != observed.get(key):
                mismatches.append(_mismatch(f"catalog-{key.replace('_', '-')}", expected[key], observed.get(key), "error"))
    if expected_names is not None:
        served_names = {operation.get("name") for operation in operations}
        missing = sorted(set(expected_names) - served_names)
        extra = sorted(served_names - set(expected_names))
        if missing or extra:
            mismatches.append(_mismatch("catalog-operations", {"missing_from_served": missing},
                                        {"extra_in_served": extra}, "error"))
    result = {
        "endpoint": profile.endpoint,
        "profile": profile.profile_id,
        "handshake": summary,
        "catalog": observed,
        "expected": expected,
        "expected_source": expected_source,
        "mismatches": mismatches,
    }
    return DiagnosticResult("catalog-probe", "1", _status(mismatches, unknowns), result,
                            work_refs=[{"kind": "environment", "id": profile.expected_environment}],
                            unknowns=unknowns)


def _count(operations: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for operation in operations:
        counts[str(operation.get(key))] = counts.get(str(operation.get(key)), 0) + 1
    return dict(sorted(counts.items()))
