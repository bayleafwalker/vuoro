"""Exercise the released ActionQ catalog through Vuoro's real service shell."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import httpx

from actionq.vuoro import catalog_metadata, register_operations
from actionq_contracts import sha256_digest
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.composition import _execution_authorizer
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


class StubApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, object]] = []

    @staticmethod
    def _decision(operation: str, result: object) -> dict:
        return {"decision": {"decision_ref": "actionq:event:1", "operation": operation,
            "request_id": "owner-stub", "status": "accepted", "code": None,
            "message": None, "event_refs": ["actionq:event:1"], "replayed": False},
            "result": result}

    def create_immutable_action(self, **kwargs):
        self.calls.append(("create", kwargs, kwargs["provenance"]))
        return self._decision("execution.action.create-immutable-candidate", {"action_id": 1})

    def realize_execution_group(self, **kwargs):
        self.calls.append(("realize", kwargs, kwargs["provenance"]))
        return self._decision("execution.group.realize", {"id": "00000000-0000-0000-0000-000000000001"})

    def __getattr__(self, name):
        def unused(*_args, **_kwargs):
            raise AssertionError(f"unexpected owner callback: {name}")
        return unused


def _pins(path: Path) -> tuple[dict, dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    adapter = next(pin for pin in manifest["adapters"] if pin["domain"] == "execution")
    return adapter, adapter["dependencies"][0]


async def _exercise() -> None:
    stub = StubApplication()
    registry = CatalogRegistry()
    register_operations(registry, application=stub)
    metadata = {item["name"]: item for item in catalog_metadata()}
    required = {"execution.action.create-immutable-candidate", "execution.group.realize",
                "execution.group.stop-new-claims", "execution.group.show", "execution.group.list"}
    assert required <= metadata.keys()
    expected = {
        "execution.action.create-immutable-candidate":
            ("execution.candidate-action.create", "enqueue", "required"),
        "execution.group.realize": ("execution.group.manage", "write", "required"),
        "execution.group.stop-new-claims":
            ("execution.group.manage", "write", "required"),
        "execution.group.show": ("execution.read", "read", "not-allowed"),
        "execution.group.list": ("execution.read", "read", "not-allowed"),
    }
    registered = {item.name: item.model_dump(mode="json") for item in registry.catalog().operations}
    for name, projection in expected.items():
        owner = metadata[name]
        assert (
            owner["required_authority"], owner["execution_semantics"], owner["idempotency"]
        ) == projection
        served = dict(registered[name])
        assert served.pop("repo_scoped") is False
        assert served == owner
    assert not any("migrate" in name or "runner" in name for name in metadata)

    app = create_app(settings=ServiceSettings(environment_name="released-wheel-gate",
        environment_class="development", compatibility_state="compatible"), registry=registry,
        identity_resolver=StaticBearerIdentityResolver({
            "compiler-token": Identity(actor="compiler:trusted", environment="released-wheel-gate",
                authorities=frozenset({"execution.candidate-action.create", "execution.group.manage"}),
                repo_ids=frozenset({"agentops"})),
            "reader-token": Identity(actor="reader:only", environment="released-wheel-gate",
                authorities=frozenset({"execution.read"})),
        }))
    digest = "a" * 64
    input_refs = [f"artifact:sha256:{digest}"]
    input_set_digest = sha256_digest(
        {"contract_id": "immutable-input-set/v1", "inputs": input_refs}
    )
    spec = {"contract_id": "candidate-integration-spec/v1", "topology": "stacked",
        "base_commit": "b" * 40, "member_result_refs": input_refs,
        "input_set_digest": input_set_digest}
    spec_digest = sha256_digest(spec)
    request = {"contract_id": "action-creation-request/v1", "plan_ref": f"artifact:sha256:{'d'*64}",
        "topology": "stacked", "role": "candidate-integration", "subject": "candidate:one",
        "spec_ref": "artifact:" + spec_digest, "spec_digest": spec_digest,
        "input_set_digest": input_set_digest}
    protocol = {"X-Vuoro-Client-Protocol": "1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def invoke(token: str, operation: str, arguments: dict, key: str):
            return await client.post("/api/invoke/v1", headers={**protocol, "Authorization": f"Bearer {token}"},
                json={"schema_version": "invocation/v1", "request_id": key, "operation": operation,
                    "arguments": arguments, "catalog_revision": registry.revision, "idempotency_key": key})
        created = await invoke("compiler-token", "execution.action.create-immutable-candidate",
            {"request": request, "spec": spec, "input_refs": spec["member_result_refs"], "project": "agentops"}, "create-1")
        assert created.status_code == 200, created.text
        group = await invoke("compiler-token", "execution.group.realize", {"contract_id": "execution-group/v1",
            "plan_ref": request["plan_ref"], "max_parallel": 1, "failure_policy": "continue-independent",
            "members": [{"action_id": 1, "envelope": {"contract_id": "execution-envelope/v1",
                "action_id": 1, "attempt_id": "attempt-1", "source_commit": "b" * 40,
                "command_id": "vuoro.service.tests", "allowed_paths": ["packages/vuoro-service"]}}]}, "group-1")
        assert group.status_code == 200, group.text
        before = len(stub.calls)
        denied = await invoke("reader-token", "execution.group.realize", {"bad": True}, "denied-1")
        assert denied.status_code == 403 and len(stub.calls) == before
        malformed = await invoke("compiler-token", "execution.group.realize",
            {"contract_id": "execution-group/v1", "spoofed_actor": "attacker"}, "bad-1")
        assert malformed.status_code == 422 and len(stub.calls) == before
    assert [call[0] for call in stub.calls] == ["create", "realize"]
    assert all(call[1]["created_by"] == "compiler:trusted" for call in stub.calls)
    assert all(call[2].actor == "compiler:trusted" and call[2].authorized_repositories == ("agentops",) for call in stub.calls)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit("usage: validate_released_execution_adapter.py MANIFEST WHEEL_DIRECTORY")
    manifest, wheel_dir = Path(argv[0]), Path(argv[1])
    adapter, dependency = _pins(manifest)
    for pin in (adapter, dependency):
        wheel = wheel_dir / pin["artifact_url"].rsplit("/", 1)[-1]
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == pin["artifact_sha256"]
        assert importlib.metadata.version(pin["distribution"]) == pin["distribution_version"]
    asyncio.run(_exercise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
