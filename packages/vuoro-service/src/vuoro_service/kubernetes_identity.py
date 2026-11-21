"""Opt-in, Pod-bound Kubernetes identity for one in-cluster read operation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import http.client
import json
from pathlib import Path
import re
import ssl
from threading import BoundedSemaphore
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Request
import jwt

from .identity import Identity, IdentityResolutionError, PRINCIPAL_ID, StaticBearerIdentityResolver

WORKLOAD_OPERATION = "work.public.item-v1"
_MAX_TOKEN = 16_384
_MAX_CONCURRENT_REVIEWS = 8
_TOKEN = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2}")
_SERVICE_ACCOUNT = re.compile(r"system:serviceaccount:[a-z0-9][a-z0-9-]*:[a-z0-9][a-z0-9.-]*")
_BINDING_FIELDS = frozenset({
    "schema_version", "api_server", "ca_file", "reviewer_token_file", "issuer",
    "audience", "service_account", "service_account_uid", "repo_id", "workspace_id",
    "principal_id", "environment",
})


class KubernetesIdentityUnavailable(IdentityResolutionError):
    code = "identity-review-unavailable"
    http_status = 503


@dataclass(frozen=True)
class KubernetesWorkloadIdentity(Identity):
    """Marks the identity so the HTTP shell can enforce its operation ceiling."""


def _required_string(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 2048 and all(32 < ord(c) < 127 for c in value)


class KubernetesIdentityResolver:
    def __init__(
        self, primary: StaticBearerIdentityResolver, *, config_path: Path, environment: str
    ) -> None:
        with config_path.open(encoding="utf-8") as file:
            binding = json.load(file)
        if (not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS
                or not all(_required_string(value) for value in binding.values())
                or binding["schema_version"] != "vuoro-kubernetes-identity/v1"
                or binding["environment"] != environment):
            raise ValueError("invalid Kubernetes identity binding")
        origin = urlsplit(binding["api_server"])
        if (origin.scheme != "https" or not origin.hostname or origin.username is not None
                or origin.password is not None or origin.path not in ("", "/")
                or origin.query or origin.fragment):
            raise ValueError("Kubernetes identity API server must be an HTTPS origin")
        issuer = urlsplit(binding["issuer"])
        audience = urlsplit(binding["audience"])
        if (issuer.scheme != "https" or not issuer.hostname or audience.scheme != "https"
                or not audience.hostname or audience.username is not None
                or audience.password is not None or audience.query or audience.fragment
                or audience.hostname.rstrip(".") in {issuer.hostname.rstrip("."), origin.hostname.rstrip(".")}
                or audience.hostname.rstrip(".") in {"kubernetes.default.svc", "kubernetes.default.svc.cluster.local"}):
            raise ValueError("Kubernetes identity requires a distinct HTTPS workload audience")
        if (not _SERVICE_ACCOUNT.fullmatch(binding["service_account"])
                or not PRINCIPAL_ID.fullmatch(binding["principal_id"])
                or "*" in (binding["repo_id"], binding["workspace_id"])):
            raise ValueError("Kubernetes identity requires an exact service account and resource binding")
        try:
            uid = str(UUID(binding["service_account_uid"]))
        except ValueError:
            raise ValueError("invalid Kubernetes ServiceAccount UID") from None
        if uid != binding["service_account_uid"]:
            raise ValueError("Kubernetes ServiceAccount UID must be canonical")
        self._reviewer_key = Path(binding["reviewer_token_file"])
        ca_file = Path(binding["ca_file"])
        if not self._reviewer_key.is_absolute() or not ca_file.is_absolute():
            raise ValueError("Kubernetes credential and CA paths must be absolute")
        self._tls = ssl.create_default_context(cafile=ca_file)
        self._primary = primary
        self._host = origin.hostname
        self._port = origin.port
        self._binding = binding
        self._review_slots = BoundedSemaphore(_MAX_CONCURRENT_REVIEWS)

    def _review(self, assertion: str) -> dict:
        with self._reviewer_key.open(encoding="ascii") as credential:
            reviewer = credential.read(_MAX_TOKEN + 1).strip()
        if len(reviewer) > _MAX_TOKEN or not _TOKEN.fullmatch(reviewer):
            raise ValueError("invalid TokenReview credential")
        connection = http.client.HTTPSConnection(self._host, self._port, context=self._tls, timeout=3)
        try:
            connection.request(
                "POST", "/apis/authentication.k8s.io/v1/tokenreviews",
                json.dumps({
                    "apiVersion": "authentication.k8s.io/v1",
                    "kind": "TokenReview",
                    "spec": {"token": assertion, "audiences": [self._binding["audience"]]},
                }),
                {"Authorization": "Bearer " + reviewer, "Content-Type": "application/json"},
            )
            result = connection.getresponse()
            if result.status != 201:
                raise ValueError("TokenReview request refused")
            body = result.read(32769)
            if len(body) > 32768:
                raise ValueError("TokenReview response exceeded its bound")
            return json.loads(body)["status"]
        finally:
            connection.close()

    def _review_in_slot(self, assertion: str) -> dict | None:
        try:
            return self._review(assertion)
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            # Do not let transport exceptions escape an abandoned background review.
            return None
        finally:
            self._review_slots.release()

    async def __call__(self, request: Request) -> Identity:
        # Preserve exact static registry matches, including opaque dotted tokens.
        try:
            return self._primary(request)
        except IdentityResolutionError:
            scheme, _, assertion = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or len(assertion) > _MAX_TOKEN or not _TOKEN.fullmatch(assertion):
                raise
        denied = IdentityResolutionError("a valid Kubernetes workload identity is required")
        binding = self._binding
        try:
            # These are rejection-only checks. The live review authenticates the same bytes.
            claims = jwt.decode(assertion, options={"verify_signature": False})
            if (claims["iss"] != binding["issuer"] or claims["sub"] != binding["service_account"]
                    or claims["aud"] != [binding["audience"]] or type(claims["exp"]) is not int):
                raise denied
            expiry = datetime.fromtimestamp(claims["exp"], UTC)
            if expiry <= datetime.now(UTC):
                raise denied
        except (KeyError, ValueError, OSError, OverflowError, jwt.PyJWTError):
            raise denied from None

        if not self._review_slots.acquire(blocking=False):
            raise KubernetesIdentityUnavailable("Kubernetes identity review is at capacity")
        # A disconnected client must not release capacity while TLS I/O is still running.
        status = await asyncio.shield(asyncio.to_thread(self._review_in_slot, assertion))
        if status is None:
            raise KubernetesIdentityUnavailable("Kubernetes identity review is unavailable")
        if not isinstance(status, dict) or status.get("authenticated") is not True or status.get("error"):
            raise denied
        user = status.get("user")
        if (status.get("audiences") != [binding["audience"]] or not isinstance(user, dict)
                or user.get("username") != binding["service_account"]
                or user.get("uid") != binding["service_account_uid"]):
            raise denied
        extra = user.get("extra")
        pod = extra.get("authentication.kubernetes.io/pod-uid") if isinstance(extra, dict) else None
        if not isinstance(pod, list) or len(pod) != 1 or not _required_string(pod[0]):
            raise denied
        if expiry <= datetime.now(UTC):
            raise denied
        return KubernetesWorkloadIdentity(
            actor=binding["service_account"], environment=binding["environment"],
            authorities=frozenset({"work:read"}), repo_ids=frozenset({binding["repo_id"]}),
            workspace_id=binding["workspace_id"], principal_id=binding["principal_id"],
            expires_at=expiry,
        )
