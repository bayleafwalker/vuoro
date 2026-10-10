"""Real old-wheel schema21 -> explicit migration22 -> runtime-only factory.

The runner supplies isolated migration/runtime DSNs and a hash-verified old
owner interpreter. No configured production endpoint is accepted.
"""
import json
import os
from pathlib import Path
import subprocess
import uuid
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

if os.environ.get('VUORO_SCHEMA22_COMPOSITION_PROOF') == '1':
    import psycopg
else:
    psycopg = pytest.importorskip('psycopg', reason='requires the PostgreSQL conformance extra')
from psycopg import sql

from vuoro_service.composition import CompositionError, create_composed_app
from .test_bound_proposal_http import assert_installed_composition

pytestmark = pytest.mark.essential_safety


def validate_fixture_urls(urls):
    """Refuse libpq query routing overrides before opening any connection."""
    addresses = set()
    roles = set()
    for index, url in enumerate(urls):
        parsed = urlsplit(url)
        assert parsed.scheme in ('postgresql', 'postgres')
        assert parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        assert parsed.port is not None and not parsed.fragment
        assert parsed.path.startswith('/lease_conformance_schema22')
        assert parsed.username and parsed.password
        query = parse_qs(parsed.query, strict_parsing=True)
        assert query == ({'options': ['-csearch_path=work,pg_catalog']} if index < 2 else {})
        normalized = psycopg.conninfo.conninfo_to_dict(url)
        assert normalized['host'] == parsed.hostname
        assert normalized['port'] == str(parsed.port)
        assert normalized['dbname'] == parsed.path[1:]
        assert normalized['user'] == parsed.username
        addresses.add((normalized['host'], normalized['port'], normalized['dbname']))
        roles.add(normalized['user'])
    assert len(addresses) == 1 and len(roles) == 4


@pytest.mark.parametrize('query', [
    'host=remote.example', 'hostaddr=192.0.2.1', 'dbname=production',
    'user=postgres', 'service=production',
    'options=-csearch_path%3Dpublic',
    'options=-csearch_path%3Dwork%2Cpg_catalog&options=-csearch_path%3Dpublic',
])
def test_fixture_guard_refuses_libpq_overrides_before_connection(query):
    urls = [f'postgresql://role{i}:synthetic@127.0.0.1:5432/lease_conformance_schema22_fixture'
            + ('?options=-csearch_path%3Dwork%2Cpg_catalog' if i < 2 else '') for i in range(4)]
    urls[0] = urls[0].split('?')[0] + '?' + query
    with pytest.raises(AssertionError):
        validate_fixture_urls(urls)


def test_actual_schema21_refusal_explicit22_migration_and_runtime_ddl_denial(tmp_path):
    names=('VUORO_SCHEMA22_MIGRATION_PG_URL','VUORO_SCHEMA22_RUNTIME_PG_URL',
           'VUORO_SCHEMA21_OWNER_PYTHON','VUORO_SCHEMA22_MANIFEST','VUORO_SCHEMA22_WHEEL_DIR',
           'VUORO_SCHEMA22_ATTESTATION','VUORO_SCHEMA22_AUDIT_MIGRATION_PG_URL',
           'VUORO_SCHEMA22_AUDIT_RUNTIME_PG_URL','VUORO_SCHEMA21_MANIFEST',
           'VUORO_SCHEMA21_WHEEL_DIR','VUORO_SCHEMA21_ATTESTATION')
    if not all(os.environ.get(x) for x in names):
        if os.environ.get('VUORO_SCHEMA22_COMPOSITION_PROOF')=='1':
            pytest.fail('mandatory schema22 upgrade fixture is incompletely configured')
        pytest.skip('requires separately provisioned disposable schema22 migration/runtime fixture')
    assert_installed_composition()
    migration,runtime,old_python,manifest,wheels,attestation,audit_migration,audit_runtime,old_manifest,old_wheels,old_attestation=(os.environ[x] for x in names)
    for name in ('PGSERVICE', 'PGSERVICEFILE', 'PGHOST', 'PGHOSTADDR', 'PGPORT',
                 'PGDATABASE', 'PGUSER', 'PGOPTIONS'):
        assert not os.environ.get(name), 'inherited libpq routing configuration is forbidden'
    validate_fixture_urls((migration,runtime,audit_migration,audit_runtime))
    child_env=dict(os.environ,SCHEMA22_PROOF_DSN=migration)
    old_setup='''import os
from importlib.metadata import version
from sprintctl import pg,pg_migrations
assert version('sprintctl')=='0.16.0'
s=pg.get_connection(os.environ['SCHEMA22_PROOF_DSN']);s.repo_id='agentops'
result=pg_migrations.migrate_schema(s)
assert result['to_version']==21
sprint=pg.create_sprint(s,'Preserved schema21','fixture','2026-01-01','2026-12-31','active')
track=pg.get_or_create_track(s,sprint,'fixture')
item=pg.create_work_item(s,sprint,track,'Preserve this actual old-owner row')
from types import SimpleNamespace
from sprintctl.application import WorkApplication
import uuid
app=WorkApplication.postgres(s)
def context(principal,authorities):
 return SimpleNamespace(identity=SimpleNamespace(actor=principal,environment='schema22-fixture',
   principal_id=principal,workspace_id='schema22-fixture',client_id=None,grant_id=None,
   authorities=frozenset(authorities)),request_id=uuid.uuid4().hex,basis_revision=None,
   catalog_revision='fixture',idempotency_requirement='not-allowed',idempotency_key=None)
proposer=context('fixture:proposer:0',{'work:evidence','work.effect.propose'})
reviewer=context('fixture:reviewer:0',{'work.effect.accept'})
run=app.invoke('work.run.register-v1',{'harness_id':'fixture','harness_build':'old-wheel',
 'model_id':'scripted','recipe_id':'schema22/v1','observed_profile':{'instruction_digest':'sha256:'+'a'*64,'skill_digests':[]},
 'idempotency_key':uuid.uuid4().hex},proposer)['run']['run_id']
pg.reserve(s,item,actor='fixture',session_id=uuid.uuid4().hex,role='execution',
           acceptance_contract={'effect_verification_required':False})
app.invoke('work.evidence.append-v1',{'run_id':run,'item_id':'old-owner-evidence','kind':'test',
 'ref':'fixture:old-evidence','digest':'sha256:'+'d'*64,'collector':'fixture',
 'validity':{'basis':'indefinite','valid_from':'2026-10-09T00:00:00Z','valid_until':None,'component_digests':{}},
 'claims':[],'provenance':{},'chain_seq':0,'chain_prev_digest':None,'idempotency_key':uuid.uuid4().hex},proposer)
intent=app.invoke('work.effect.propose-v1',{'run_id':run,'item_id':item,'repository':'example',
 'base_commit':'b'*40,'title':'Preserved intent','rationale':'Fixture','unified_diff':'--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b\\n',
 'idempotency_key':uuid.uuid4().hex},proposer)['intent']
app.invoke('work.effect.accept-v1',{k:intent[k] for k in ('intent_id','revision','canonical_intent_digest')},reviewer)
s.conn.close()
'''
    result=subprocess.run([old_python,'-I','-c',old_setup],env=child_env,capture_output=True,text=True)
    assert result.returncode==0,'old released owner fixture initialization refused'
    from auditctl.central_schema import migrate as audit_migrate
    from sprintctl import pg,pg_migrations
    runtime_role=urlsplit(runtime).username
    migration_role=urlsplit(migration).username
    audit_runtime_role=urlsplit(audit_runtime).username
    audit_migration_role=urlsplit(audit_migration).username
    with psycopg.connect(audit_migration) as conn:
        audit_migrate(conn,schema='audit',migration_role=urlsplit(audit_migration).username,
                      runtime_role=audit_runtime_role)
        conn.execute('INSERT INTO audit.ingest_stream(origin_stream_id) VALUES (%s)',(uuid.uuid4(),))

    def audit_rows():
        with psycopg.connect(audit_migration) as conn:
            tables=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='audit' ORDER BY tablename")]
            return {table: sorted((r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t').format(sql.Identifier('audit',table))).fetchall()),key=lambda v:json.dumps(v,sort_keys=True)) for table in tables}

    audit_before=audit_rows()
    for role_url in (migration,runtime,audit_migration,audit_runtime):
        with psycopg.connect(role_url) as conn:
            assert not any(conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
    for schema,owner,reader,reader_url in (('work',migration_role,runtime_role,runtime),('audit',audit_migration_role,audit_runtime_role,audit_runtime)):
        with psycopg.connect(reader_url) as conn:
            assert conn.execute('SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s',(schema,)).fetchone()[0]==owner
            for migrator in (migration_role,audit_migration_role):
                assert not conn.execute("SELECT pg_has_role(current_user,%s,'MEMBER')",(migrator,)).fetchone()[0]

    def grants():
        with psycopg.connect(migration) as conn:
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA work TO {}').format(sql.Identifier(runtime_role)))
            conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA work TO {}').format(sql.Identifier(runtime_role)))
            conn.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA work TO {}').format(sql.Identifier(runtime_role)))
            conn.execute(sql.SQL('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA work TO {}').format(sql.Identifier(runtime_role)))

    def legacy_rows():
        with psycopg.connect(migration) as conn:
            tables=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='work' ORDER BY tablename")]
            rows={}
            for table in tables:
                if table in ('schema_version','work_effect_attempt','work_effect_attempt_event'):continue
                values=conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t').format(sql.Identifier(table))).fetchall()
                rows[table]=sorted((r[0] for r in values),key=lambda v:json.dumps(v,sort_keys=True))
            return rows

    grants()
    before=legacy_rows()
    identity=tmp_path/'identities.json'
    identity.write_text(json.dumps({'schema_version':'vuoro-identities/v1','identities':{'x'*40:{
        'actor':'fixture-reader','principal_id':'fixture:reader:0','environment':'schema22-fixture',
        'authorities':['work:read'],'repo_ids':['agentops']},'a'*40:{
        'actor':'fixture-applier','principal_id':'fixture:applier:0','environment':'schema22-fixture',
        'workspace_id':'schema22-fixture','authorities':['work.effect.mark-applied'],'repo_ids':['agentops']}}}))
    observers=tmp_path/'observers.json'
    observers.write_text(json.dumps({'schema_version':'vuoro-work-resource-observers/v1','grants':[]}))
    bindings=Path(__file__).resolve().parents[2]/'composition/project-bindings.json'
    env={'VUORO_ENVIRONMENT_NAME':'schema22-fixture','VUORO_ENVIRONMENT_CLASS':'development',
        'VUORO_WORK_REPOSITORY_ID':'agentops','VUORO_WORK_RUNTIME_DSN':runtime,
        'VUORO_AUDIT_RUNTIME_DSN':audit_runtime,'VUORO_AUDIT_SCHEMA':'audit',
        'VUORO_INSTALLED_COMPOSITION_PATH':attestation,'VUORO_WORK_RESOURCE_OBSERVERS_FILE':str(observers)}
    def factory():
        return create_composed_app(manifest_path=Path(manifest),wheel_dir=Path(wheels),
            identity_path=identity,project_bindings_path=bindings,environ=env)
    def old_factory(expected):
        # Process exit closes every connection even after eager factory refusal.
        code='''import json,os
from pathlib import Path
from fastapi.testclient import TestClient
from vuoro_service.composition import create_composed_app,CompositionError
v=json.loads(os.environ['SCHEMA22_FACTORY_INPUT'])
try:
 app=create_composed_app(manifest_path=Path(v['manifest']),wheel_dir=Path(v['wheels']),
  identity_path=Path(v['identity']),project_bindings_path=Path(v['bindings']),environ=v['env'])
except CompositionError as e:
 assert v['expected']=='refused' and str(e)=='runtime compatibility failed for: work'
else:
 assert v['expected']=='ready'
 with TestClient(app) as client:
  assert client.get('/health/ready').status_code==200
  handshake=client.get('/api/meta/v1/handshake').json()
  assert handshake['service_version']=='0.1.90'
  assert handshake['compatibility']['state']=='compatible'
'''
        value={'manifest':old_manifest,'wheels':old_wheels,'identity':str(identity),
               'bindings':str(bindings),'env':{**env,'VUORO_INSTALLED_COMPOSITION_PATH':old_attestation},
               'expected':expected}
        child=dict(os.environ,SCHEMA22_FACTORY_INPUT=json.dumps(value))
        result=subprocess.run([old_python,'-I','-c',code],env=child,capture_output=True,text=True)
        assert result.returncode==0,'actual old service factory result did not match expected compatibility'
    old_factory('ready')
    with pytest.raises(CompositionError,match='runtime compatibility failed for: work'):
        factory()
    assert legacy_rows()==before
    with psycopg.connect(migration) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0]==21
        assert conn.execute("SELECT to_regclass('work_effect_attempt')").fetchone()[0] is None
    store=pg.get_connection(migration);store.repo_id='agentops'
    try:
        migrated=pg_migrations.migrate_schema(store)
        assert migrated['from_version']==21 and migrated['to_version']==22 and migrated['applied_versions']==[22]
        assert pg_migrations.migrate_schema(store)['applied_versions']==[]
    finally:store.conn.close()
    grants()
    assert legacy_rows()==before
    with TestClient(factory()) as client:
        response=client.get('/api/meta/v1/handshake',headers={'X-Vuoro-Client-Protocol':'1'})
        assert response.status_code==200,response.json()
        handshake=response.json()
        assert handshake['service_version']=='0.1.93' and handshake['compatibility']['state']=='compatible'
        assert all(x['state']=='compatible' for x in handshake['compatibility']['domains'].values())
        catalog=client.get('/api/catalog/v1',headers={'X-Vuoro-Client-Protocol':'1'}).json()
        assert len(catalog['operations'])==85
        assert handshake['catalog_revision']=='4230516fcb775d72e14dfa774c8395a9f50ad5b640ccd9134648a8c659f22af3'
        response=client.get('/health/ready')
        assert response.status_code==200
    with psycopg.connect(runtime) as conn:
        role=conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        assert not any(role)
        assert not conn.execute("SELECT pg_has_role(current_user,%s,'MEMBER')",(migration_role,)).fetchone()[0]
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0]==22
        assert not conn.execute("SELECT has_schema_privilege(current_user,'work','CREATE')").fetchone()[0]
        for table in ('work_effect_attempt','work_effect_attempt_event'):
            assert conn.execute('SELECT has_table_privilege(current_user,%s,%s)',(table,'SELECT,INSERT,UPDATE,DELETE')).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():conn.execute('CREATE TABLE work.must_refuse_schema22_fixture(id integer)')
        assert conn.execute("SELECT to_regclass('work.must_refuse_schema22_fixture')").fetchone()[0] is None
    with psycopg.connect(audit_runtime) as conn:
        assert not conn.execute("SELECT has_schema_privilege(current_user,'audit','CREATE')").fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():conn.execute('CREATE TABLE audit.must_refuse_schema22_fixture(id integer)')
        assert conn.execute("SELECT to_regclass('audit.must_refuse_schema22_fixture')").fetchone()[0] is None
    old_factory('refused')
    assert legacy_rows()==before
    intent=before['work_effect_intent'][0]
    store=pg.get_connection(migration);store.repo_id='agentops'
    try:release=pg.get_release(store,intent['release_digest'])
    finally:store.conn.close()
    with TestClient(factory()) as client:
        def invoke(operation,arguments):
            response=client.post('/api/invoke/v1',headers={'X-Vuoro-Client-Protocol':'1',
                'Authorization':'Bearer '+'a'*40},json={'schema_version':'invocation/v1',
                'request_id':uuid.uuid4().hex,'repo_id':'agentops',
                'catalog_revision':'4230516fcb775d72e14dfa774c8395a9f50ad5b640ccd9134648a8c659f22af3',
                'operation':operation,'arguments':arguments})
            assert response.status_code==200,response.json()
            return response.json()['result']
        opened=invoke('work.effect.attempt-open-v1',{
            **{k:intent[k] for k in ('intent_id','revision','canonical_intent_digest')},
            'expected_revision':release['item_revision'],'release_digest':release['release_digest'],
            'target':{'operation':'push_branch','branch':'schema22/'+intent['intent_id'],'commit_sha':'c'*40},
            'idempotency_key':uuid.uuid4().hex})
        attempt_id=opened['authorization']['attempt_id']
        redeemed=invoke('work.effect.attempt-redeem-v1',{'attempt_id':attempt_id,
            'authorization_digest':opened['authorization_digest'],'idempotency_key':uuid.uuid4().hex})
        assert redeemed['dispatch_permitted'] is True
        observed=invoke('work.effect.attempt-get-v1',{'attempt_id':attempt_id})
        assert observed['state']=='redeemed' and len(observed['events'])==2
    after=legacy_rows()
    for table,rows in before.items():
        if table=='work_idempotency_ledger':
            assert all(row in after[table] for row in rows) and len(after[table])==len(rows)+2
        else:assert after[table]==rows
    child_env['SCHEMA22_PROOF_DSN']=runtime
    old_refusal='''import os
from sprintctl import pg,pg_migrations
s=pg.get_connection(os.environ['SCHEMA22_PROOF_DSN'])
try:pg_migrations.require_compatible_schema(s)
except pg_migrations.RemoteSchemaCompatibilityError:pass
else:raise SystemExit('old owner accepted schema22')
finally:s.conn.close()
'''
    result=subprocess.run([old_python,'-I','-c',old_refusal],env=child_env,capture_output=True,text=True)
    assert result.returncode==0,'old owner did not refuse postmigration schema22'
    assert legacy_rows()==after
    assert audit_rows()==audit_before
