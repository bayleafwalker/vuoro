"""Fresh fixed native basis read; accepted invocation and public states are DATA."""

from uuid import uuid4

from .protected_copy_codec import (
    PARAMETERS,
    _assert_effect_data,
    _canonical,
    _closed,
    _need,
    _read,
    _read_json,
    _text,
)
from .protected_currentness import decode_current_basis
from .protected_read_transport import (
    ReadoutProfile,
    _NATIVE_PATH,
    _async_fixed,
    _fixed,
    _read_wire,
)

_OPERATION = "work.effect.basis-v1"
_WRAPPER_MAX = 4 * 1024 * 1024


class NativeBasisSource:
    """One registered namespace's private read; no arbitrary invocation API.

    Profile construction and a well-formed returned basis confer no authority.
    Current work and selected release changes remain data for currentness policy.
    """

    @_fixed
    def __init__(self, profile, credential_provider):
        _need(
            type(profile) is ReadoutProfile
            and callable(getattr(credential_provider, "acquire", None))
        )
        profile.__post_init__()
        self._profile, self._credential_provider = profile, credential_provider

    @property
    def profile(self):
        return self._profile

    @_async_fixed
    async def read_basis(self, effect):
        _assert_effect_data(effect)
        _need(
            effect.namespace_utf8 == self.profile.namespace_utf8
            and effect.repository == self.profile.repository
        )
        namespace = _read(self.profile.namespace_utf8)
        locator = _read(effect.locator_utf8)
        request_id = str(uuid4())
        request = {
            "schema_version": "invocation/v1",
            "request_id": request_id,
            "operation": _OPERATION,
            "arguments": {
                "intent_id": locator["intent_id"],
                "intent_revision": locator["intent_revision"],
            },
            "repo_id": namespace["repo_id"],
            "catalog_revision": None,
            "basis_revision": None,
            "idempotency_key": None,
        }
        raw = await _read_wire(
            self.profile, self._credential_provider, _NATIVE_PATH, _canonical(request)
        )
        wrapper = _read_json(raw, _WRAPPER_MAX)
        _closed(
            wrapper,
            "schema_version request_id operation catalog_revision status result error",
        )
        _need(
            wrapper["schema_version"] == "invocation-result/v1"
            and wrapper["request_id"] == request_id
            and wrapper["operation"] == _OPERATION
            and wrapper["status"] == "accepted"
            and wrapper["error"] is None
        )
        _text(wrapper["catalog_revision"], 256)
        basis = decode_current_basis(_canonical(wrapper["result"]), effect=effect)
        _need(
            basis.repo_id == namespace["repo_id"]
            and basis.workspace_id == namespace["workspace_id"]
        )
        intent, run = _read(basis.intent_utf8), _read(basis.run_utf8)
        original_run = _read(effect.run_document_utf8)["native_run"]
        original_release = _read(effect.release_document_utf8)
        _need(
            intent["intent_id"] == locator["intent_id"]
            and intent["revision"] == locator["intent_revision"]
            and _canonical({key: intent[key] for key in PARAMETERS.split()})
            == effect.parameters_utf8
            and intent["canonical_intent_digest"] == effect.legacy_content_digest
            and intent["release_digest"] == original_release["release_digest"]
            and intent["run_id"] == effect.source_run
            and _canonical({key: run[key] for key in original_run})
            == _canonical(original_run)
        )
        return basis
