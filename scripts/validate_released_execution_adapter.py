"""Exercise the released ActionQ catalog through Vuoro's real service shell."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import zipfile

import httpx

from actionq.vuoro import catalog_metadata, compatibility_record, register_operations
from actionq_contracts import sha256_digest
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.composition import _execution_authorizer
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


_EXPECTED_ADAPTER_KIT = (
    "https://github.com/bayleafwalker/vuoro/releases/download/"
    "vuoro-adapter-kit-v0.1.0/vuoro_adapter_kit-0.1.0-py3-none-any.whl"
)
_EXPECTED_SCHEMA_RUNTIME = (
    "https://github.com/bayleafwalker/vuoro/releases/download/"
    "vuoro-schema-runtime-v0.1.0/vuoro_schema_runtime-0.1.0-py3-none-any.whl"
)
_EXPECTED_SCHEMA_RUNTIME_REQUIREMENT = (
    f"vuoro-schema-runtime @ {_EXPECTED_SCHEMA_RUNTIME}#sha256="
    "b66c9357c99aa9e1a7353991ce54105a8621958ecfac47f8c121d80b90b77912"
)
_EXPECTED_CATALOG_OPERATION_COUNT = 26
_EXPECTED_CATALOG_METADATA_SHA256 = (
    "8d434e8b347e804c90e48a6598304be84b12f2a61ebc2dbed00a26053239a778"
)
_EXPECTED_OLD_CATALOG_METADATA_SHA256 = (
    "1b25af2143d4a8895fba83954d69c420e3ff0a364f6fc94269d39d7cac2ed8e3"
)
_EXPECTED_OLD_OPERATION_COUNT = 22
_COMPLETION_OPERATIONS = (
    "execution.session-completion.ingest",
    "execution.session-completion.list",
    "execution.session-completion.replay",
    "execution.session-completion.health",
)
_COMPLETION_AUTHORITIES = {
    "execution.session-completion.ingest": "execution.session-completion.ingest",
    "execution.session-completion.list": "execution.session-completion.read",
    "execution.session-completion.replay": "execution.session-completion.read",
    "execution.session-completion.health": "execution.session-completion.read",
}
_OLD_OPERATION_SHA256 = {
    "execution.action.enqueue": "93e95dbd9371537ab76a2bd89dda0fd0bcbbe9db7169bfd0c3dc2226eba4f21a",
    "execution.dispatch.enqueue": "6f203830b9932b3a6fb63b35aae2a5d80fb89a1834e1ba09d54953f98f6b826d",
    "execution.action.create-immutable-candidate": "f8b6b56d86d4876db3c8dafcfbf09208709b7f2d7f9a052f8e81f9f7d786f7ef",
    "execution.action.list": "e6b03f3b38cd806264803a40fb0b8f490052f849db9f72e030c5923f4b09d002",
    "execution.action.show": "9ff4d4d856cc82c9141f749b98dfe95d0d287b49997f24d33a10e3a394d808bf",
    "execution.group.realize": "cae0d3c53d7864d0bd102f858d841b75b5cfd8c2f8c73a3d95edb4e15300182f",
    "execution.group.stop-new-claims": "e6a7dee48f2f3dbea96ca0522e6d4dfd723e3c2689b86d095ab30bf08c98726b",
    "execution.group.show": "9425426ef4ac455425a4aebee69006e5f88939b85d30e4e0b699a30c74bfda38",
    "execution.group.list": "abf1ce35800a1a6df1e111c8a5664b2e792f85533ec338965a95fd2d9aa2c819",
    "execution.action.claim": "4430ebe7242cc8a218a38f26fa7008b5215c6c2ad30a656d60d663a7c2f41bd7",
    "execution.action.renew": "2f65f3ace025a1878c216ec2762fa1ced2700f73809858e4bc35fe79f9c43f59",
    "execution.action.settle": "4cc8fe17422de69ca6e53999dd39db99f5d07ec18458eca32625ed250b61fab3",
    "execution.action.complete": "025fc42e54a540a25d6b3e05c2b8ea000abeb3afe19629a43e3e1c97a6bc03a9",
    "execution.action.fail": "13790b9273509614e353ad980b47495ddaf6255dfbd0acf366fad4a9187295a2",
    "execution.action.reject": "aff131e3134df96ad8cdf0d804475b220c48aa164335bbbb5e05c9bcf12cbde5",
    "execution.action.cancel": "715efc1f5bf36f35050a6afd61d91804165684a07f70b0934fe7956116c31992",
    "execution.action.sweep": "9d5ec1b34b507158a4c211a2dfe306d704dbb1aa12d3936806c8255881264057",
    "execution.event.list": "5e07346bee06617eda570f78a71f1427cb98c800c6c3c9f3e51db8d39abd5064",
    "execution.session.list": "9fdb3d138d04c32b00a8d20bc273f224b92aef0c49ddbc0b3def7fb6375c89e6",
    "execution.session.record": "a4accddacbdbc661c4d3ef932362e0b9c2e73fa7dfb49e23ced24bf8503006f2",
    "execution.dispatch.enqueue.v1": "859fc0f1a63f864593ede2985186b3a7f38fed73380be842954c05120d381975",
    "execution.dispatch.list": "c002545391fab83fcbb6b4d56cd9d3b9045ed0c2b171ac49d3b6dc1caa7e5748",
}


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

    @staticmethod
    def compatibility():
        return {
            "observed_schema_version": 12,
            "maximum_schema_version": 12,
            "compatible": True,
            "detail": None,
        }


def _pins(path: Path) -> tuple[dict, dict, dict, dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "vuoro-composition/v3":
        raise SystemExit("unsupported composition schema_version")
    descriptor = next(item for item in manifest["runtime_descriptors"] if item["domain"] == "execution")
    locks = {lock["lock_id"]: lock for lock in manifest["release_locks"]}
    if descriptor["dependency_lock_ids"] != [
        "execution-contracts", "vuoro-adapter-kit", "vuoro-schema-runtime"
    ]:
        raise SystemExit(
            "execution descriptor must identify exactly the contract, adapter-kit, and schema-runtime locks"
        )
    adapter = locks[descriptor["lock_id"]]
    contracts = locks[descriptor["dependency_lock_ids"][0]]
    shared = locks[descriptor["dependency_lock_ids"][1]]
    schema_runtime = locks[descriptor["dependency_lock_ids"][2]]
    if adapter.get("lock_kind") != "adapter":
        raise SystemExit("execution descriptor primary lock must be an adapter")
    if contracts.get("lock_kind") != "owner-dependency":
        raise SystemExit("execution descriptor dependency must be an owner dependency")
    if contracts.get("distribution_version") != "0.1.1":
        raise SystemExit("execution descriptor must keep execution-contracts 0.1.1")
    if shared.get("lock_kind") != "shared-dependency":
        raise SystemExit("execution descriptor dependency must include the shared adapter kit")
    if shared.get("artifact_url") != _EXPECTED_ADAPTER_KIT:
        raise SystemExit("execution dependency is not the released adapter-kit wheel")
    if schema_runtime.get("lock_kind") != "shared-dependency":
        raise SystemExit("execution schema runtime must be a shared dependency")
    if schema_runtime.get("artifact_url") != _EXPECTED_SCHEMA_RUNTIME:
        raise SystemExit("execution dependency is not the released schema-runtime wheel")
    requirements = importlib.metadata.requires(adapter["distribution"]) or []
    if _EXPECTED_SCHEMA_RUNTIME_REQUIREMENT not in requirements:
        raise SystemExit("execution owner metadata does not require the locked schema runtime")
    if descriptor.get("schema_version") != "actionq-schema/v12":
        raise SystemExit("execution descriptor must select actionq-schema/v12")
    return adapter, contracts, shared, schema_runtime


async def _exercise() -> None:
    stub = StubApplication()
    registry = CatalogRegistry()
    register_operations(registry, application=stub)
    owner_metadata = catalog_metadata()
    assert len(owner_metadata) == _EXPECTED_CATALOG_OPERATION_COUNT
    assert hashlib.sha256(
        json.dumps(owner_metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest() == _EXPECTED_CATALOG_METADATA_SHA256
    metadata = {item["name"]: item for item in owner_metadata}
    assert set(_COMPLETION_OPERATIONS) <= metadata.keys()
    assert len(_COMPLETION_OPERATIONS) == 4
    assert {
        name: metadata[name]["required_authority"] for name in _COMPLETION_OPERATIONS
    } == _COMPLETION_AUTHORITIES
    assert compatibility_record(stub)["schema_version"] == "12"
    assert compatibility_record(stub)["state"] == "compatible"
    # The old 22-operation catalog is a compatibility subset: its canonical
    # bytes remain unchanged, while the four completion operations are additive.
    old_names = [name for name in metadata if name not in _COMPLETION_OPERATIONS]
    assert len(old_names) == _EXPECTED_OLD_OPERATION_COUNT
    assert set(old_names) == set(_OLD_OPERATION_SHA256)
    assert hashlib.sha256(
        json.dumps(
            [item for item in owner_metadata if item["name"] not in _COMPLETION_OPERATIONS],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest() == _EXPECTED_OLD_CATALOG_METADATA_SHA256
    assert {
        name: hashlib.sha256(
            json.dumps(metadata[name], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for name in old_names
    } == _OLD_OPERATION_SHA256
    required = {"execution.action.create-immutable-candidate", "execution.group.realize",
                "execution.group.stop-new-claims", "execution.group.show", "execution.group.list"}
    assert required <= metadata.keys()
    projections = {
        "execution.action.create-immutable-candidate":
            ("execution.candidate-action.create", "enqueue", "required"),
        "execution.group.realize": ("execution.group.manage", "write", "required"),
        "execution.group.stop-new-claims":
            ("execution.group.manage", "write", "required"),
        "execution.group.show": ("execution.read", "read", "not-allowed"),
        "execution.group.list": ("execution.read", "read", "not-allowed"),
    }
    registered = {item.name: item.model_dump(mode="json") for item in registry.catalog().operations}
    expected = {
        name: {**definition, "repo_scoped": False}
        for name, definition in metadata.items()
    }
    managed_dispatch_operation = "execution.managed-dispatch.enqueue"
    # The ordinary metadata catalog deliberately remains unchanged until the
    # served composition supplies its managed-dispatch policy.  Registration
    # carries the additional operation so that the policy-aware service can
    # bind it without widening the ordinary dispatch catalog.
    assert managed_dispatch_operation not in metadata
    assert set(registered) == set(expected) | {managed_dispatch_operation}
    assert registered[managed_dispatch_operation]["required_authority"] == "execution.enqueue"
    assert registered[managed_dispatch_operation]["execution_semantics"] == "enqueue"
    assert registered[managed_dispatch_operation]["idempotency"] == "required"
    for name, definition in expected.items():
        assert registered[name] == definition
    for name, projection in projections.items():
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
        stale = await client.post(
            "/api/invoke/v1",
            headers={**protocol, "Authorization": "Bearer reader-token"},
            json={
                "schema_version": "invocation/v1",
                "request_id": "stale-catalog",
                "operation": "execution.group.list",
                "arguments": {},
                "catalog_revision": "0" * 64,
            },
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "stale-catalog"
    assert [call[0] for call in stub.calls] == ["create", "realize"]
    assert all(call[1]["created_by"] == "compiler:trusted" for call in stub.calls)
    assert all(call[2].actor == "compiler:trusted" and call[2].authorized_repositories == ("agentops",) for call in stub.calls)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit("usage: validate_released_execution_adapter.py MANIFEST WHEEL_DIRECTORY")
    manifest, wheel_dir = Path(argv[0]), Path(argv[1])
    adapter, contracts, adapter_kit, schema_runtime = _pins(manifest)
    for pin in (adapter, contracts, adapter_kit, schema_runtime):
        wheel = wheel_dir / pin["artifact_url"].rsplit("/", 1)[-1]
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == pin["artifact_sha256"]
        assert importlib.metadata.version(pin["distribution"]) == pin["distribution_version"]
    with zipfile.ZipFile(wheel_dir / adapter["artifact_url"].rsplit("/", 1)[-1]) as wheel:
        schema = wheel.read("actionq/schema.py").decode()
        migration = wheel.read("actionq/migrations/011_session_completion_log.sql").decode()
        managed_dispatch_migration = wheel.read(
            "actionq/migrations/012_managed_dispatch_envelopes.sql"
        ).decode()
    assert "MAX_SCHEMA_VERSION = 12" in schema
    assert "ACTIONQ_COMPLETION_INGEST_ROLE" in schema
    assert "ACTIONQ_COMPLETION_READ_ROLE" in schema
    assert "completion database roles must be distinct from ACTIONQ_RUNTIME_ROLE" in schema
    for relation in (
        "session_completion_events",
        "session_completion_stream_positions",
        "session_completion_acknowledgements",
        "session_completion_quarantine",
        "session_completion_watermarks",
    ):
        assert relation in migration
    assert "managed_dispatch_envelopes" in managed_dispatch_migration
    asyncio.run(_exercise())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
