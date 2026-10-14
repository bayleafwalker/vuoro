"""One protected acceptance's durable publication orchestration.

Trusted dependencies supply scoped fresh delivery, current basis and provider
capabilities. Neither accepted DATA nor a journal marker grants authority.
"""

import asyncio
import base64
from dataclasses import dataclass
import weakref

from . import execution_journal as journal
from .protected_copy_codec import (
    _canonical,
    _hash,
    _need,
    _read,
    _text,
    decode_protected_copy,
)
from .protected_currentness import evaluate_currentness
from .protected_ledger_source import ProtectedCopyRead

_LOCKS = weakref.WeakKeyDictionary()


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionResult:
    status: str
    reason: str
    effect_key: str | None
    outcome_utf8: bytes | None


def _events(record):
    return {
        event.event_type: _read(event.payload_utf8, 65536) for event in record.events
    }


def _request(record, effect, operation):
    seen = _events(record)
    binding, local = seen["BOUND"], seen["LOCAL_READY"]
    common = {
        "effect_key": record.effect_key,
        "binding_sha256": _hash(record.binding_utf8),
        "repository": effect.repository,
        "branch": "vuoro-protected/" + record.effect_key,
        "commit_oid": local["commit_oid"],
        "provider_profile_sha256": binding["provider_profile_sha256"],
    }
    if operation == "capability-check":
        return _canonical(
            {
                "schema": "vuoro-protected-provider-capability-request/v1",
                "effect_key": record.effect_key,
                "binding_sha256": _hash(record.binding_utf8),
                "repository": effect.repository,
                "provider_profile_sha256": binding["provider_profile_sha256"],
            }
        )
    if operation.startswith("branch-"):
        return _canonical(
            {
                "schema": "vuoro-protected-branch-request/v1",
                **common,
                "base_parent_oid": local["base_parent_oid"],
            }
        )
    parameters = _read(effect.parameters_utf8)
    public = _canonical(journal._public_binding(record, binding, local, effect))
    body = (
        parameters["rationale"]
        + "\n\n<!-- vuoro-protected-publication/v1 "
        + base64.b64encode(public).decode("ascii")
        + " -->\n"
    ).encode("utf8")
    return _canonical(
        {
            "schema": "vuoro-protected-pr-request/v1",
            **common,
            "head_repository": binding["publication_target"]["head_repository"],
            "base_branch": binding["publication_target"]["base_branch"],
            "title": parameters["title"],
            "body_utf8_b64": base64.b64encode(body).decode("ascii"),
            "body_sha256": _hash(body),
        }
    )


def _provider_result(result, record, effect, operation, *, preliminary=False):
    request = _request(record, effect, operation)
    _need(
        result.operation == operation
        and result.effect_key == record.effect_key
        and result.binding_sha256 == _hash(record.binding_utf8)
        and result.request_sha256 == _hash(request)
    )
    allowed = (
        {"CAPABILITY_PRESENT", "UNKNOWN", "REFUSED"}
        if operation == "capability-check"
        else {"ABSENT", "OBSERVED", "UNKNOWN", "CONFLICT", "REFUSED"}
        if operation.endswith("-lookup")
        else {"TRANSMISSION_POSSIBLE", "UNKNOWN", "CONFLICT", "REFUSED"}
    )
    _need(type(result.status) is str and result.status in allowed)
    if result.status == "OBSERVED":
        _need(type(result.observation_utf8) is bytes)
        kind = "BRANCH_OBSERVED" if operation == "branch-lookup" else "PR_OBSERVED"
        seen = _events(record)
        if preliminary:
            # Validate DATA only; this synthetic request never enters the journal
            # and cannot permit adoption of an unrecorded remote operation.
            marker = (
                "BRANCH_TRANSMISSION_POSSIBLE"
                if kind == "BRANCH_OBSERVED"
                else "PR_TRANSMISSION_POSSIBLE"
            )
            seen[marker] = _read(request, 65536)
        journal._payload(
            record,
            kind,
            result.observation_utf8,
            seen,
            effect,
            _read(record.binding_utf8),
        )
    else:
        _need(result.observation_utf8 is None)
    return result


class ProtectedExecutor:
    """Trusted composition only; no CLI, credential selection or activation."""

    def __init__(
        self, ledger_source, native_basis_source, journal_store, protected_git, provider
    ):
        self.ledger = ledger_source
        self.basis = native_basis_source
        self.journal = journal_store
        self.git = protected_git
        self.provider = provider
        self._pins = self._registered()

    def _registered(self):
        profile = self.provider.profile
        data = profile.data
        _need(
            _hash(profile.profile_utf8) == profile.profile_sha256
            and _hash(self.journal.registry_utf8) == profile.registry_sha256
        )
        _need(profile.signing == self.git.profile)
        _need(
            self.ledger.profile.namespace_utf8 == self.basis.profile.namespace_utf8
            and self.ledger.profile.repository == self.basis.profile.repository
            and self.ledger.profile.repository == data["effect_repository"]
        )
        journal._branch(data["base_branch"])
        return (
            profile.profile_id,
            profile.registry_sha256,
            profile.profile_sha256,
            profile.profile_utf8,
            self.git.profile.signing_profile_utf8,
            self.git.profile.public_key_utf8,
            self.ledger.profile.namespace_utf8,
            self.ledger.profile.repository,
        )

    def _check_registered(self):
        _need(self._registered() == self._pins)

    async def _copy(self, acceptance_id, original=None):
        self._check_registered()
        delivery = await self.ledger.read_copy(acceptance_id)
        _need(type(delivery) is ProtectedCopyRead)
        decoded = decode_protected_copy(
            delivery.wire_utf8,
            namespace_utf8=self.ledger.profile.namespace_utf8,
            acceptance_id=acceptance_id,
            repository=self.ledger.profile.repository,
        )
        _need(decoded == delivery.effect)
        if original is not None:
            _need(delivery.wire_utf8 == original.protected_copy_utf8)
        self._check_registered()
        return delivery

    def _bind(self, delivery):
        profile = self.provider.profile
        signing = _read(self.git.profile.signing_profile_utf8)
        effect = delivery.effect
        target = _canonical(
            {
                "schema": "vuoro-protected-publication-target/v1",
                "repository": effect.repository,
                "head_repository": effect.repository,
                "base_branch": profile.data["base_branch"],
            }
        )
        return self.journal.bind(
            effect,
            protected_copy_utf8=delivery.wire_utf8,
            provider_profile_sha256=profile.profile_sha256,
            signing_profile_sha256=self.git.profile.signing_profile_sha256,
            publication_target_utf8=target,
            expected_signer_fingerprint=signing["signing_fingerprint"],
            expected_public_key_sha256=signing["public_key_sha256"],
        )

    async def _gate_new_mutation(self, acceptance_id, record, effect, artifact):
        delivery = await self._copy(acceptance_id, record)
        _need(delivery.effect == effect)
        basis = await self.basis.read_basis(effect)
        current = evaluate_currentness(effect, basis)
        if current.status != "READY":
            return None
        capability = _provider_result(
            await self.provider.fresh_capability(record),
            record,
            effect,
            "capability-check",
        )
        if capability.status != "CAPABILITY_PRESENT":
            return None
        self._check_registered()
        return artifact

    def _result(self, record, reason="publication-unsettled"):
        status = record.status
        if status == "APPLIED":
            reason = "original-publication-proved"
        elif status in {"REFUSED", "CONFLICT"}:
            reason = _read(record.events[-1].payload_utf8)["reason"]
        else:
            status = "UNSETTLED" if record.spent else "DEFERRED"
        return ExecutionResult(status, reason, record.effect_key, record.outcome_utf8)

    def _terminal(self, record, reason, operation=None):
        event = "REFUSED" if reason == "original-pr-closed" else "CONFLICT"
        request = None
        if operation is not None:
            request = next(
                spent.request_utf8
                for spent in record.spent
                if spent.operation == operation
            )
        raw = _canonical(
            {
                "schema": "vuoro-protected-journal-terminal/v1",
                "effect_key": record.effect_key,
                "binding_sha256": _hash(record.binding_utf8),
                "reason": reason,
                "operation": operation,
                "request_sha256": _hash(request) if request is not None else None,
            }
        )
        return self.journal.record_observed(record.effect_key, event, raw)

    def _applied(self, record):
        seen = _events(record)
        raw = _canonical(
            {
                "schema": "vuoro-protected-applied-outcome/v1",
                "effect_key": record.effect_key,
                "binding_sha256": _hash(record.binding_utf8),
                "commit_oid": seen["LOCAL_READY"]["commit_oid"],
                "branch_request_sha256": _hash(
                    _canonical(seen["BRANCH_TRANSMISSION_POSSIBLE"])
                ),
                "pr_request_sha256": _hash(
                    _canonical(seen["PR_TRANSMISSION_POSSIBLE"])
                ),
                "pr_number": seen["PR_OBSERVED"]["pr_number"],
                "pr_url": seen["PR_OBSERVED"]["pr_url"],
            }
        )
        return self.journal.record_outcome(record.effect_key, raw)

    async def run(self, acceptance_id):
        _text(acceptance_id, 200)
        loop = asyncio.get_running_loop()
        locks = _LOCKS.setdefault(loop, {})
        lock = locks.setdefault(str(self.journal.directory), asyncio.Lock())
        async with lock:
            knowledge = {"key": None, "transmission_possible": False}
            try:
                with self.journal.cycle():
                    return await self._execute(acceptance_id, knowledge)
            except asyncio.CancelledError:
                raise
            except Exception:
                return ExecutionResult(
                    "UNSETTLED" if knowledge["transmission_possible"] else "DEFERRED",
                    "execution-unavailable",
                    knowledge["key"],
                    None,
                )

    async def _execute(self, acceptance_id, knowledge):
        delivery = await self._copy(acceptance_id)
        effect = delivery.effect
        knowledge["key"] = journal.effect_key(effect)
        previous = self.journal.read(knowledge["key"])
        if previous is not None:
            knowledge["transmission_possible"] = bool(previous.spent)
        record = self._bind(delivery)
        seen = _events(record)
        if record.status in {"APPLIED", "REFUSED", "CONFLICT"}:
            return self._result(record, "durable-terminal")
        if "LOCAL_READY" in seen:
            try:
                artifact = await self.git.verify_original(effect, record)
            except asyncio.CancelledError:
                raise
            except Exception:
                return self._result(
                    self._terminal(record, "original-artifact-mismatch"),
                    "original-artifact-mismatch",
                )
        else:
            artifact = None
        # These immutable observations prove historical publication. Subsequent
        # lifecycle changes cannot erase it or authorize another transmission.
        if (
            "BRANCH_OBSERVED" in seen
            and "PR_OBSERVED" in seen
            and seen["PR_OBSERVED"]["state"] in {"open", "merged"}
        ):
            return self._result(self._applied(record), "original-publication-proved")
        if "PR_OBSERVED" in seen and seen["PR_OBSERVED"]["state"] == "closed":
            return self._result(
                self._terminal(record, "original-pr-closed", "pr-create"),
                "original-pr-closed",
            )
        basis = await self.basis.read_basis(effect)
        current = evaluate_currentness(effect, basis)
        if artifact is None:
            if current.status != "READY":
                return self._result(record, "basis-not-ready")
            artifact = await self.git.prepare_original(effect, record)
            record = self.journal.record_local_ready(
                record.effect_key, artifact.local_ready_utf8
            )
        for operation in ("branch-create", "pr-create"):
            record = self.journal.read(record.effect_key)
            lookup_operation = (
                "branch-lookup" if operation == "branch-create" else "pr-lookup"
            )
            kind = "BRANCH_OBSERVED" if operation == "branch-create" else "PR_OBSERVED"
            marker = (
                "BRANCH_TRANSMISSION_POSSIBLE"
                if operation == "branch-create"
                else "PR_TRANSMISSION_POSSIBLE"
            )
            seen = _events(record)
            if marker not in seen:
                if current.status != "READY":
                    return self._result(record, "basis-not-ready")
                artifact = await self.git.verify_original(effect, record)
                preliminary = _provider_result(
                    await self._lookup(record, operation),
                    record,
                    effect,
                    lookup_operation,
                    preliminary=True,
                )
                if preliminary.status != "ABSENT":
                    return self._result(record, "unrecorded-provider-state")
                artifact = await self._gate_new_mutation(
                    acceptance_id, record, effect, artifact
                )
                if artifact is None:
                    return self._result(record, "publication-not-ready")
                request = _request(record, effect, operation)
                # A commit/fsync exception may occur after persistence. Retain
                # this conservative knowledge without rereading an uncertain DB.
                knowledge["transmission_possible"] = True
                first = self.journal.mark_spent(record.effect_key, operation, request)
                if first is not True:
                    return self._result(
                        self.journal.read(record.effect_key), "marker-already-spent"
                    )
                record = self.journal.read(record.effect_key)
                # No exception or ambiguous response permits a second invocation.
                try:
                    if operation == "branch-create":
                        result = await self.provider.create_branch_once(
                            record, artifact
                        )
                    else:
                        result = await self.provider.create_pr_once(record)
                    _provider_result(result, record, effect, operation)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            try:
                result = _provider_result(
                    await self._lookup(record, operation),
                    record,
                    effect,
                    lookup_operation,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return self._result(record, "provider-observation-unavailable")
            if result.status == "CONFLICT":
                reason = (
                    "original-branch-mismatch"
                    if operation == "branch-create"
                    else "original-pr-mismatch"
                )
                return self._result(self._terminal(record, reason, operation), reason)
            if result.status != "OBSERVED":
                return self._result(record)
            if kind not in _events(record):
                record = self.journal.record_observed(
                    record.effect_key, kind, result.observation_utf8
                )
            elif kind == "BRANCH_OBSERVED":
                _need(
                    result.observation_utf8
                    == next(
                        e.payload_utf8 for e in record.events if e.event_type == kind
                    )
                )
            if kind == "PR_OBSERVED":
                if _events(record)[kind]["state"] == "closed":
                    return self._result(
                        self._terminal(record, "original-pr-closed", operation),
                        "original-pr-closed",
                    )
                return self._result(
                    self._applied(record), "original-publication-proved"
                )
        return self._result(record)

    async def _lookup(self, record, operation):
        if operation == "branch-create":
            return await self.provider.lookup_branch(record)
        return await self.provider.lookup_pr(record)
