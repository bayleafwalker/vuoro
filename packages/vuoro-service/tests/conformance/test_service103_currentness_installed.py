"""Actual installed 0.1.103/0.28 composition on an explicitly prepared disposable DB."""
from __future__ import annotations
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from .installed_currentness import exercise_currentness
from .installed_report_outcome_v2 import exercise_report_outcome_v2
from .knowledge_fixture import validate_knowledge_urls, provision_knowledge
from .test_schema28_composition_migration import validate_fixture_urls

NAMES=('VUORO_103_WORK_MIGRATION','VUORO_103_WORK_RUNTIME','VUORO_103_AUDIT_MIGRATION','VUORO_103_AUDIT_RUNTIME',
       'VUORO_103_KNOWLEDGE_MIGRATION','VUORO_103_KNOWLEDGE_RUNTIME','VUORO_103_MANIFEST','VUORO_103_WHEELS',
       'VUORO_103_ATTESTATION','VUORO_103_OWNER_PREFIX')


def test_installed_composition_currentness_http_is_read_only(tmp_path,monkeypatch):
    if not all(os.environ.get(k) for k in NAMES):
        if os.environ.get('VUORO_103_PROOF')=='1':pytest.fail('required installed composition fixture missing')
        pytest.skip('requires separately prepared disposable installed composition')
    work_migration,work_runtime,audit_migration,audit_runtime,knowledge_migration,knowledge_runtime,manifest,wheels,attestation,prefix=(os.environ[k] for k in NAMES)
    validate_fixture_urls([work_migration,work_runtime,audit_migration,audit_runtime])
    validate_knowledge_urls(knowledge_migration,knowledge_runtime,'lease_conformance_schema28')
    assert not any(k.startswith('PG') for k in os.environ)
    for dist,module,wanted in [('sprintctl','sprintctl','0.28.0'),('vuoro-service','vuoro_service','0.1.103'),
                               ('vuoro-workspace','vuoro_workspace','0.1.1'),('auditctl','auditctl','0.1.9')]:
        assert version(dist)==wanted
        assert Path(importlib.import_module(module).__file__).resolve().is_relative_to(Path(prefix).resolve())
    receipt=json.loads(Path(attestation).read_text());assert receipt['verified'] is True
    assert next(x for x in receipt['distributions'] if x['distribution']=='sprintctl')['installed_version']=='0.28.0'
    import psycopg
    for url in (work_migration,work_runtime,audit_migration,audit_runtime,knowledge_migration,knowledge_runtime):
        with psycopg.connect(url) as conn:
            assert not any(conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
    from auditctl.central_schema import migrate as audit_migrate
    from urllib.parse import urlsplit
    with psycopg.connect(audit_migration) as conn:
        audit_migrate(conn, schema='audit', migration_role=urlsplit(audit_migration).username, runtime_role=urlsplit(audit_runtime).username)
    knowledge_env=provision_knowledge(knowledge_migration,knowledge_runtime,tmp_path/'knowledge-objects')
    from sprintctl.application import WorkApplication
    from vuoro_service.composition import create_composed_app
    roots=[];original=WorkApplication.postgres.__func__
    def capture(cls,store,**kwargs):
        result=original(cls,store,**kwargs)
        if store.repo_id=='agentops':roots.append(result)
        return result
    monkeypatch.setattr(WorkApplication,'postgres',classmethod(capture))
    identity=tmp_path/'identities.json';identity.write_text(json.dumps({'schema_version':'vuoro-identities/v1','identities':{'c'*40:{'actor':'fixture-claim','principal_id':'github:100:0','environment':'schema28-fixture','workspace_id':'schema28-fixture','authorities':['work:read','work:claim','work:evidence'],'repo_ids':['agentops']},'r'*40:{'actor':'fixture-reader','principal_id':'fixture:reader:0','environment':'schema28-fixture','workspace_id':'schema28-fixture','authorities':['work:read'],'repo_ids':['agentops']}}}))
    observers=tmp_path/'observers.json';observers.write_text(json.dumps({'schema_version':'vuoro-work-resource-observers/v1','grants':[]}))
    bindings=Path(__file__).resolve().parents[2]/'composition/project-bindings.json'
    environ={'VUORO_ENVIRONMENT_NAME':'schema28-fixture','VUORO_ENVIRONMENT_CLASS':'development',
      'VUORO_WORK_REPOSITORY_ID':'agentops','VUORO_WORK_RUNTIME_DSN':work_runtime,'VUORO_AUDIT_RUNTIME_DSN':audit_runtime,
      'VUORO_AUDIT_SCHEMA':'audit','VUORO_INSTALLED_COMPOSITION_PATH':attestation,
      'VUORO_WORK_RESOURCE_OBSERVERS_FILE':str(observers),**knowledge_env}
    app=create_composed_app(manifest_path=Path(manifest),wheel_dir=Path(wheels),identity_path=identity,
                            project_bindings_path=bindings,environ=environ)
    assert roots
    with TestClient(app) as client:
        ready=client.get('/api/meta/v1/handshake',headers={'X-Vuoro-Client-Protocol':'1'})
        assert ready.status_code==200 and ready.json()['service_version']=='0.1.103'
        catalog=client.get('/api/catalog/v1',headers={'X-Vuoro-Client-Protocol':'1'}).json()
        revoked_token=exercise_currentness(client,roots[0])
        diagnostic=exercise_report_outcome_v2(client,roots[0],revoked_token);print("installed ReportOutcome-v2 diagnostic", diagnostic)
        print('installed runtime catalog revision', catalog['revision'])
        assert catalog['revision']=='f1ced0795fad1ef55b30805d80955fb8a04d2554c9adaea52a2794441a01fd26'
        assert len(catalog['operations'])==150 and len({x['name'] for x in catalog['operations']})==150
