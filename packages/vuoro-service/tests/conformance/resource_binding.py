"""Public owner observations on a unique disposable repository, through HTTP.

This is a coordination-horizon binding, not a new generic resource provider.
Missing aggregate semantics are reported as gaps, never fabricated here.
"""
from urllib.parse import urlsplit
import uuid
from fastapi.testclient import TestClient
from vuoro_service.app import create_app, ServiceSettings
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


class ResourceBinding:
    def __init__(self, url):
        from sprintctl import pg
        from sprintctl.application import WorkApplication
        from sprintctl.vuoro_adapter import register_work_catalog
        parsed = urlsplit(url)
        if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith("/lease_conformance"):
            raise ValueError("resource conformance requires a disposable loopback lease_conformance database")
        self.pg = pg
        self.store = pg.get_connection(url)
        self.store.repo_id = "resource-conformance-" + uuid.uuid4().hex
        pg.init_db(self.store)
        self.application = WorkApplication.postgres(self.store)
        self.registry = CatalogRegistry()
        register_work_catalog(self.registry, self.application)
        identities = {}
        for token, epoch, caps in (("original", 0, {"work:read", "work:write", "work:lifecycle"}),
                                   ("reissued", 1, {"work:read", "work:write", "work:lifecycle"}),
                                   ("reader", 0, {"work:read"})):
            identities[token] = Identity(actor="same-display-name", environment="contract", authorities=frozenset(caps),
                repo_ids=frozenset({self.store.repo_id}), workspace_id="contract", principal_id=f"issuer:subject:{epoch}")
        self.client = TestClient(create_app(settings=ServiceSettings(environment_name="contract"), registry=self.registry,
            identity_resolver=StaticBearerIdentityResolver(identities)))
        self.sprint = pg.create_sprint(self.store, "Resource contract", "Disposable", "2026-01-01", "2026-12-31", "active")
        self.track = pg.get_or_create_track(self.store, self.sprint, "contract")

    def item(self):
        response = self.invoke("work.item.create", {"sprint_id": self.sprint, "track_name": "contract", "title": "Resource contract"})
        assert response.status_code == 200, response.text
        return response.json()["result"]["item"]["id"]

    def invoke(self, operation, arguments, *, token="original", revision=None):
        return self.client.post("/api/invoke/v1", headers={"Authorization": "Bearer " + token,
            "X-Vuoro-Client-Protocol": "1"}, json={"schema_version": "invocation/v1",
            "request_id": uuid.uuid4().hex, "repo_id": self.store.repo_id, "operation": operation,
            "arguments": arguments, "catalog_revision": self.registry.revision if revision is None else revision})

    def read(self, item):
        response = self.invoke("work.read.item", {"item_id": item})
        assert response.status_code == 200, response.text
        return response.json()["result"]

    def close(self):
        self.client.close()
        self.store.conn.close()
