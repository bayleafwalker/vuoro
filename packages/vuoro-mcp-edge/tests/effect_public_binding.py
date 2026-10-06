"""Real HTTP shell binding for the neutral public/protected effect contract.

Reference operations use the existing intent-store model. The PG variant
registers the published owner's full catalog, without copying its schema or
transition logic. Test bearer identities stand for authority sets, not OAuth
issuance or a deployed gateway. No external effect is executed.
"""
from __future__ import annotations

import asyncio

import httpx

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, SCHEMA_DIALECT, OperationRejectedError
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from vuoro_mcp_edge.toolsets import BUCKET_AUTHORITIES, ToolFailure


class EffectHttpBinding:
    def __init__(self, authority):
        self.authority = authority
        self.calls = []
        self.registry = CatalogRegistry()
        self.repo_id = "repo"
        schema_version = "reference-effect/v1"
        if hasattr(authority, "owner_store"):
            from sprintctl.vuoro_adapter import register_work_catalog, WORK_SCHEMA_VERSION

            binding = self

            class ObservedApplication:
                def __getattr__(self, name):
                    return getattr(authority.app, name)

                def invoke(self, operation, arguments, context):
                    binding.calls.append(operation)
                    return authority.app.invoke(operation, arguments, context)

            register_work_catalog(self.registry, ObservedApplication())
            self.repo_id = authority.owner_store.repo_id
            schema_version = WORK_SCHEMA_VERSION
        else:
            for operation in ("accept", "mark-applied"):
                name = "work.effect." + operation + "-v1"

                def handler(arguments, context, *, operation=operation, name=name):
                    self.calls.append(name)
                    try:
                        return {"intent": authority.transition(
                            operation, arguments, authorities=context.identity.authorities)}
                    except ToolFailure as error:
                        raise OperationRejectedError(error.code, str(error), http_status=403) from error

                self.registry.register(OperationDefinition(
                    name=name, owning_domain="work",
                    input_schema={"$schema": SCHEMA_DIALECT, "type": "object"},
                    result_schema={"$schema": SCHEMA_DIALECT, "type": "object"},
                    required_authority="work.effect." + operation,
                    execution_semantics="write", idempotency="not-allowed", repo_scoped=True,
                ), handler)

        # The proposed TS-16 maximum plus ordinary work/proposal/get rights:
        # none confers the existing private accept/mark-applied capabilities.
        public = frozenset(BUCKET_AUTHORITIES.values()) | {
            "work:write", "work.effect.propose", "work.effect.get"}
        self.public_authorities = public
        resolver = StaticBearerIdentityResolver({
            "test-public": Identity(actor="public", environment="contract",
                principal_id="test:public:0", workspace_id="workspace",
                client_id="test-public-client", grant_id="test-public-grant",
                repo_ids=frozenset({self.repo_id}), authorities=public),
            "test-protected": Identity(actor="operator", environment="contract",
                principal_id="test:operator:0", workspace_id="workspace",
                repo_ids=frozenset({self.repo_id}),
                authorities=frozenset({"work.effect.accept", "work.effect.mark-applied"})),
        })
        self.service = create_app(registry=self.registry, identity_resolver=resolver,
            settings=ServiceSettings(environment_name="contract", environment_class="development",
                compatibility_state="compatible", domains={"work": DomainCompatibility(
                    api_version="work/v1", schema_version=schema_version, state="compatible")}))

    def transition(self, operation, row, *, public=True):
        args = {key: row[key] for key in ("intent_id", "revision", "canonical_intent_digest")}
        if operation == "mark-applied":
            args.update(commit_sha="b" * 40, pr_url="https://forge.example/repo/pulls/1")

        async def invoke():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.service),
                                         base_url="http://contract.test") as client:
                return await client.post("/api/invoke/v1", headers={
                    "X-Vuoro-Client-Protocol": "1",
                    "Authorization": "Bearer " + ("test-public" if public else "test-protected"),
                    # Caller-controlled strings cannot widen resolved identity.
                    "X-Vuoro-Authorities": "work.effect.accept work.effect.mark-applied",
                }, json={"schema_version": "invocation/v1", "request_id": "boundary-request",
                    "operation": "work.effect." + operation + "-v1", "arguments": args,
                    "repo_id": self.repo_id, "catalog_revision": self.registry.revision})

        return asyncio.run(invoke())
