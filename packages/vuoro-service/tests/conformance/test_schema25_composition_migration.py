"""Real old-closure schema22 -> explicit CLI migration25 -> runtime-only factory.

The old closure is the released vuoro-service 0.1.93 with Sprintctl 0.18.0
(work schema 22), the composition vuoro-shared runs before this upgrade. The
runner supplies isolated migration/runtime DSNs and a hash-verified old owner
interpreter. The new composition's startup must refuse the schema-22 store,
the migration runs only through the released owner's explicit
``sprintctl remote-schema migrate`` command, and startup then succeeds with
the agent directory and inbox operations served. No configured production
endpoint is accepted.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

if os.environ.get('VUORO_SCHEMA25_COMPOSITION_PROOF') == '1':
    import psycopg
else:
    psycopg = pytest.importorskip('psycopg', reason='requires the PostgreSQL conformance extra')
from psycopg import sql

from vuoro_service.composition import CompositionError, create_composed_app
from .inbox_contract import new_instance_id
from .test_bound_proposal_http import assert_installed_composition

pytestmark = pytest.mark.essential_safety

CATALOG_REVISION = 'fd49d79de14a0ec630dde511928cc76e3aceb2e159266f883dcbe6ab14853d2b'
CATALOG_TOTAL = 102
INBOX_OPERATIONS = ('work.agent.register-v1', 'work.agent.observe-v1', 'work.agent.retire-v1',
                    'work.agent.list-v1', 'work.agent.describe-v1', 'work.message.send-v1',
                    'work.message.read-v1', 'work.message.status-v1', 'work.message.receipt-v1',
                    'work.message.cancel-v1')
# Tables the explicit migration from schema 22 creates: 23 run/invocation
# correlations, 25 agent directory and inbox (24 adds only an index). The
# preserved-row comparison covers every table that existed before migration.
POST22_TABLES = ('work_run_invocation_binding', 'work_run_invocation_attempt',
                 'agent_instance', 'agent_declaration_revision', 'agent_observation',
                 'agent_inbox_counter', 'agent_message', 'agent_message_receipt')
NAMES = ('VUORO_SCHEMA25_MIGRATION_PG_URL', 'VUORO_SCHEMA25_RUNTIME_PG_URL',
         'VUORO_SCHEMA22_OWNER_PYTHON', 'VUORO_SCHEMA25_MANIFEST', 'VUORO_SCHEMA25_WHEEL_DIR',
         'VUORO_SCHEMA25_ATTESTATION', 'VUORO_SCHEMA25_AUDIT_MIGRATION_PG_URL',
         'VUORO_SCHEMA25_AUDIT_RUNTIME_PG_URL', 'VUORO_SCHEMA22_MANIFEST',
         'VUORO_SCHEMA22_WHEEL_DIR', 'VUORO_SCHEMA22_ATTESTATION')


def validate_fixture_urls(urls):
    """Refuse libpq query routing overrides before opening any connection."""
    addresses = set()
    roles = set()
    for index, url in enumerate(urls):
        parsed = urlsplit(url)
        assert parsed.scheme in ('postgresql', 'postgres')
        assert parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        assert parsed.port is not None and not parsed.fragment
        assert parsed.path.startswith('/lease_conformance_schema25')
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
    urls = [f'postgresql://role{i}:synthetic@127.0.0.1:5432/lease_conformance_schema25_fixture'
            + ('?options=-csearch_path%3Dwork%2Cpg_catalog' if i < 2 else '') for i in range(4)]
    urls[0] = urls[0].split('?')[0] + '?' + query
    with pytest.raises(AssertionError):
        validate_fixture_urls(urls)


OLD_SETUP = '''import json,os
from importlib.metadata import version
from sprintctl import pg,pg_migrations
assert version('sprintctl')=='0.18.0'
s=pg.get_connection(os.environ['SCHEMA25_PROOF_DSN']);s.repo_id='agentops'
result=pg_migrations.migrate_schema(s)
assert result['to_version']==22
sprint=pg.create_sprint(s,'Preserved schema22','fixture','2026-01-01','2026-12-31','active')
track=pg.get_or_create_track(s,sprint,'fixture')
item=pg.create_work_item(s,sprint,track,'Preserve this actual old-owner row')
from types import SimpleNamespace
from sprintctl.application import WorkApplication
import uuid
app=WorkApplication.postgres(s)
def context(principal,authorities):
 return SimpleNamespace(identity=SimpleNamespace(actor=principal,environment='schema25-fixture',
   principal_id=principal,workspace_id='schema25-fixture',client_id=None,grant_id=None,
   authorities=frozenset(authorities)),request_id=uuid.uuid4().hex,basis_revision=None,
   catalog_revision='fixture',idempotency_requirement='not-allowed',idempotency_key=None)
proposer=context('fixture:proposer:0',{'work:evidence','work.effect.propose'})
reviewer=context('fixture:reviewer:0',{'work.effect.accept'})
applier=context('fixture:applier:0',{'work.effect.mark-applied'})
run=app.invoke('work.run.register-v1',{'harness_id':'fixture','harness_build':'old-wheel',
 'model_id':'scripted','recipe_id':'schema25/v1','observed_profile':{'instruction_digest':'sha256:'+'a'*64,'skill_digests':[]},
 'idempotency_key':uuid.uuid4().hex},proposer)['run']['run_id']
pg.reserve(s,item,actor='fixture',session_id=uuid.uuid4().hex,role='execution',
           acceptance_contract={'effect_verification_required':False})
app.invoke('work.evidence.append-v1',{'run_id':run,'item_id':'old-owner-evidence','kind':'test',
 'ref':'fixture:old-evidence','digest':'sha256:'+'d'*64,'collector':'fixture',
 'validity':{'basis':'indefinite','valid_from':'2026-10-14T00:00:00Z','valid_until':None,'component_digests':{}},
 'claims':[],'provenance':{},'chain_seq':0,'chain_prev_digest':None,'idempotency_key':uuid.uuid4().hex},proposer)
intent=app.invoke('work.effect.propose-v1',{'run_id':run,'item_id':item,'repository':'example',
 'base_commit':'b'*40,'title':'Preserved intent','rationale':'Fixture','unified_diff':'--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b\\n',
 'idempotency_key':uuid.uuid4().hex},proposer)['intent']
app.invoke('work.effect.accept-v1',{k:intent[k] for k in ('intent_id','revision','canonical_intent_digest')},reviewer)
with s.conn.cursor() as cur:
 cur.execute('SELECT release_digest FROM work_effect_intent WHERE intent_id=%s',(intent['intent_id'],))
 release=pg.get_release(s,cur.fetchone()['release_digest'])
opened=app.invoke('work.effect.attempt-open-v1',{
 **{k:intent[k] for k in ('intent_id','revision','canonical_intent_digest')},
 'expected_revision':release['item_revision'],'release_digest':release['release_digest'],
 'target':{'operation':'push_branch','branch':'schema25/'+intent['intent_id'],'commit_sha':'c'*40},
 'idempotency_key':uuid.uuid4().hex},applier)
attempt_id=opened['authorization']['attempt_id']
redeemed=app.invoke('work.effect.attempt-redeem-v1',{'attempt_id':attempt_id,
 'authorization_digest':opened['authorization_digest'],'idempotency_key':uuid.uuid4().hex},applier)
assert redeemed['dispatch_permitted'] is True
s.conn.commit()
s.conn.close()
print(json.dumps({'attempt_id':attempt_id}))
'''

OLD_FACTORY = '''import json,os
from pathlib import Path
from fastapi.testclient import TestClient
from vuoro_service.composition import create_composed_app,CompositionError
v=json.loads(os.environ['SCHEMA25_FACTORY_INPUT'])
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
  assert handshake['service_version']=='0.1.93'
  assert handshake['compatibility']['state']=='compatible'
'''

OLD_REFUSAL = '''import os
from sprintctl import pg,pg_migrations
s=pg.get_connection(os.environ['SCHEMA25_PROOF_DSN'])
try:pg_migrations.require_compatible_schema(s)
except pg_migrations.RemoteSchemaCompatibilityError:pass
else:raise SystemExit('old owner accepted schema25')
finally:s.conn.close()
'''


def test_actual_schema22_refusal_explicit25_cli_migration_and_runtime_ddl_denial(tmp_path):
    if not all(os.environ.get(x) for x in NAMES):
        if os.environ.get('VUORO_SCHEMA25_COMPOSITION_PROOF') == '1':
            pytest.fail('mandatory schema25 upgrade fixture is incompletely configured')
        pytest.skip('requires separately provisioned disposable schema25 migration/runtime fixture')
    assert_installed_composition()
    (migration, runtime, old_python, manifest, wheels, attestation, audit_migration, audit_runtime,
     old_manifest, old_wheels, old_attestation) = (os.environ[x] for x in NAMES)
    for name in ('PGSERVICE', 'PGSERVICEFILE', 'PGHOST', 'PGHOSTADDR', 'PGPORT',
                 'PGDATABASE', 'PGUSER', 'PGOPTIONS', 'SPRINTCTL_URL'):
        assert not os.environ.get(name), 'inherited libpq routing configuration is forbidden'
    validate_fixture_urls((migration, runtime, audit_migration, audit_runtime))
    # The released owner's own console script, installed in this composition.
    owner_cli = Path(sys.executable).parent / 'sprintctl'
    assert owner_cli.is_file(), 'the new composition must install the released owner CLI'

    child_env = dict(os.environ, SCHEMA25_PROOF_DSN=migration)
    result = subprocess.run([old_python, '-I', '-c', OLD_SETUP], env=child_env,
                            capture_output=True, text=True)
    assert result.returncode == 0, 'old released owner fixture initialization refused'
    attempt_id = json.loads(result.stdout.strip().splitlines()[-1])['attempt_id']

    from auditctl.central_schema import migrate as audit_migrate
    runtime_role = urlsplit(runtime).username
    migration_role = urlsplit(migration).username
    audit_runtime_role = urlsplit(audit_runtime).username
    audit_migration_role = urlsplit(audit_migration).username
    with psycopg.connect(audit_migration) as conn:
        audit_migrate(conn, schema='audit', migration_role=audit_migration_role,
                      runtime_role=audit_runtime_role)
        conn.execute('INSERT INTO audit.ingest_stream(origin_stream_id) VALUES (%s)', (uuid.uuid4(),))

    def audit_rows():
        with psycopg.connect(audit_migration) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='audit' ORDER BY tablename")]
            return {table: sorted((r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t').format(
                sql.Identifier('audit', table))).fetchall()), key=lambda v: json.dumps(v, sort_keys=True))
                for table in tables}

    audit_before = audit_rows()
    for role_url in (migration, runtime, audit_migration, audit_runtime):
        with psycopg.connect(role_url) as conn:
            assert not any(conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                                        'FROM pg_roles WHERE rolname=current_user').fetchone())
    for schema, owner, reader_url in (('work', migration_role, runtime),
                                      ('audit', audit_migration_role, audit_runtime)):
        with psycopg.connect(reader_url) as conn:
            assert conn.execute('SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s',
                                (schema,)).fetchone()[0] == owner
            for migrator in (migration_role, audit_migration_role):
                assert not conn.execute("SELECT pg_has_role(current_user,%s,'MEMBER')", (migrator,)).fetchone()[0]

    def grants():
        with psycopg.connect(migration) as conn:
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA work TO {}').format(sql.Identifier(runtime_role)))
            conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA work TO {}').format(
                sql.Identifier(runtime_role)))
            conn.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA work TO {}').format(
                sql.Identifier(runtime_role)))
            conn.execute(sql.SQL('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA work TO {}').format(
                sql.Identifier(runtime_role)))

    def legacy_rows():
        with psycopg.connect(migration) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='work' ORDER BY tablename")]
            rows = {}
            for table in tables:
                if table in ('schema_version', *POST22_TABLES):
                    continue
                values = conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t').format(sql.Identifier(table))).fetchall()
                rows[table] = sorted((r[0] for r in values), key=lambda v: json.dumps(v, sort_keys=True))
            return rows

    def remote_schema(command, url):
        env = {k: v for k, v in os.environ.items() if k != 'SPRINTCTL_URL'}
        return subprocess.run([str(owner_cli), 'remote-schema', command, '--url', url, '--json'],
                              env=env, capture_output=True, text=True)

    grants()
    before = legacy_rows()
    assert before['work_effect_attempt'] and before['work_effect_attempt_event']
    identity = tmp_path / 'identities.json'
    identity.write_text(json.dumps({'schema_version': 'vuoro-identities/v1', 'identities': {
        'x' * 40: {'actor': 'fixture-reader', 'principal_id': 'fixture:reader:0', 'environment': 'schema25-fixture',
                   'authorities': ['work:read'], 'repo_ids': ['agentops']},
        'a' * 40: {'actor': 'fixture-applier', 'principal_id': 'fixture:applier:0', 'environment': 'schema25-fixture',
                   'workspace_id': 'schema25-fixture', 'authorities': ['work.effect.mark-applied'],
                   'repo_ids': ['agentops']},
        'b' * 40: {'actor': 'fixture-agent', 'principal_id': 'github:2:0', 'environment': 'schema25-fixture',
                   'workspace_id': 'schema25-fixture', 'authorities': ['work:read', 'work:evidence'],
                   'repo_ids': ['agentops']}}}))
    observers = tmp_path / 'observers.json'
    observers.write_text(json.dumps({'schema_version': 'vuoro-work-resource-observers/v1', 'grants': []}))
    bindings = Path(__file__).resolve().parents[2] / 'composition/project-bindings.json'
    env = {'VUORO_ENVIRONMENT_NAME': 'schema25-fixture', 'VUORO_ENVIRONMENT_CLASS': 'development',
           'VUORO_WORK_REPOSITORY_ID': 'agentops', 'VUORO_WORK_RUNTIME_DSN': runtime,
           'VUORO_AUDIT_RUNTIME_DSN': audit_runtime, 'VUORO_AUDIT_SCHEMA': 'audit',
           'VUORO_INSTALLED_COMPOSITION_PATH': attestation, 'VUORO_WORK_RESOURCE_OBSERVERS_FILE': str(observers)}

    def factory():
        return create_composed_app(manifest_path=Path(manifest), wheel_dir=Path(wheels),
                                   identity_path=identity, project_bindings_path=bindings, environ=env)

    def old_factory(expected):
        # Process exit closes every connection even after eager factory refusal.
        value = {'manifest': old_manifest, 'wheels': old_wheels, 'identity': str(identity),
                 'bindings': str(bindings), 'env': {**env, 'VUORO_INSTALLED_COMPOSITION_PATH': old_attestation},
                 'expected': expected}
        child = dict(os.environ, SCHEMA25_FACTORY_INPUT=json.dumps(value))
        result = subprocess.run([old_python, '-I', '-c', OLD_FACTORY], env=child, capture_output=True, text=True)
        assert result.returncode == 0, 'actual old service factory result did not match expected compatibility'

    # The live closure serves the schema-22 store; the new composition refuses it
    # at startup and does not migrate.
    old_factory('ready')
    with pytest.raises(CompositionError, match='runtime compatibility failed for: work'):
        factory()
    checked = remote_schema('check', runtime)
    assert checked.returncode == 1 and json.loads(checked.stdout)['compatible'] is False
    assert legacy_rows() == before
    with psycopg.connect(migration) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 22
        for table in POST22_TABLES:
            assert conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None

    # Explicit, separately invoked owner migration with the migration-role credential.
    migrated = remote_schema('migrate', migration)
    assert migrated.returncode == 0, 'explicit remote-schema migrate refused'
    migrated = json.loads(migrated.stdout)
    assert migrated['from_version'] == 22 and migrated['to_version'] == 25
    assert migrated['applied_versions'] == [23, 24, 25]
    again = remote_schema('migrate', migration)
    assert again.returncode == 0 and json.loads(again.stdout)['applied_versions'] == []
    grants()
    checked = remote_schema('check', runtime)
    assert checked.returncode == 0 and json.loads(checked.stdout)['compatible'] is True
    assert legacy_rows() == before

    with TestClient(factory()) as client:
        response = client.get('/api/meta/v1/handshake', headers={'X-Vuoro-Client-Protocol': '1'})
        assert response.status_code == 200, response.json()
        handshake = response.json()
        assert handshake['service_version'] == '0.1.95' and handshake['compatibility']['state'] == 'compatible'
        assert all(x['state'] == 'compatible' for x in handshake['compatibility']['domains'].values())
        catalog = client.get('/api/catalog/v1', headers={'X-Vuoro-Client-Protocol': '1'}).json()
        assert len(catalog['operations']) == CATALOG_TOTAL
        assert handshake['catalog_revision'] == CATALOG_REVISION
        served = {operation['name'] for operation in catalog['operations']}
        assert set(INBOX_OPERATIONS) <= served
        assert client.get('/health/ready').status_code == 200
    with psycopg.connect(runtime) as conn:
        role = conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                            'FROM pg_roles WHERE rolname=current_user').fetchone()
        assert not any(role)
        assert not conn.execute("SELECT pg_has_role(current_user,%s,'MEMBER')", (migration_role,)).fetchone()[0]
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0] == 25
        assert not conn.execute("SELECT has_schema_privilege(current_user,'work','CREATE')").fetchone()[0]
        for table in POST22_TABLES:
            assert conn.execute('SELECT has_table_privilege(current_user,%s,%s)',
                                (table, 'SELECT,INSERT,UPDATE,DELETE')).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute('CREATE TABLE work.must_refuse_schema25_fixture(id integer)')
        assert conn.execute("SELECT to_regclass('work.must_refuse_schema25_fixture')").fetchone()[0] is None
    with psycopg.connect(audit_runtime) as conn:
        assert not conn.execute("SELECT has_schema_privilege(current_user,'audit','CREATE')").fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute('CREATE TABLE audit.must_refuse_schema25_fixture(id integer)')
        assert conn.execute("SELECT to_regclass('audit.must_refuse_schema25_fixture')").fetchone()[0] is None
    old_factory('refused')
    assert legacy_rows() == before

    with TestClient(factory()) as client:
        def invoke(operation, arguments, token):
            response = client.post('/api/invoke/v1', headers={'X-Vuoro-Client-Protocol': '1',
                'Authorization': 'Bearer ' + token}, json={'schema_version': 'invocation/v1',
                'request_id': uuid.uuid4().hex, 'repo_id': 'agentops', 'catalog_revision': CATALOG_REVISION,
                'operation': operation, 'arguments': arguments})
            assert response.status_code == 200, response.json()
            return response.json()['result']
        # Schema-22 attempt facts written by the old owner stay readable.
        observed = invoke('work.effect.attempt-get-v1', {'attempt_id': attempt_id}, 'a' * 40)
        assert observed['state'] == 'redeemed' and len(observed['events']) == 2
        # The schema-25 agent directory is served through the composed runtime.
        instance_id = new_instance_id()
        invoke('work.agent.register-v1', {'instance_id': instance_id, 'idempotency_key': 'schema25-register',
            'declaration': {'schema': 'agent-declaration/v1', 'harness': {'name': 'claude-code', 'version': '1.0'},
                            'adapter': None, 'host_class': 'workstation', 'interactivity': 'interactive',
                            'accepted_kinds': ['continue'],
                            'channels': [{'channel': 'mcp', 'wake_modes': ['read-while-running']}]}},
            'b' * 40)
        listed = invoke('work.agent.list-v1', {}, 'b' * 40)
        assert any(entry['principal_id'] == 'github:2:0' for entry in listed['entries'])
    after = legacy_rows()
    for table, rows in before.items():
        if table == 'work_idempotency_ledger':
            assert all(row in after[table] for row in rows)
        else:
            assert after[table] == rows, table
    with psycopg.connect(migration) as conn:
        assert conn.execute('SELECT count(*) FROM agent_instance WHERE instance_id=%s',
                            (instance_id,)).fetchone()[0] == 1
    child_env['SCHEMA25_PROOF_DSN'] = runtime
    result = subprocess.run([old_python, '-I', '-c', OLD_REFUSAL], env=child_env, capture_output=True, text=True)
    assert result.returncode == 0, 'old owner did not refuse postmigration schema25'
    assert legacy_rows() == after
    assert audit_rows() == audit_before
