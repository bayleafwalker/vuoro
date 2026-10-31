"""Published schema29 -> schema31 owner migration; disposable roles only."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest
if os.environ.get('VUORO_SCHEMA31_PROOF') == '1':
    import psycopg
else:
    psycopg = pytest.importorskip('psycopg')
from psycopg import sql

pytestmark = pytest.mark.essential_safety

OLD_SEED = r'''
import json, os
from datetime import datetime, timezone
from sprintctl import pg, operator_request as c, operator_request_pg as p
s=pg.get_connection(os.environ['MIGRATION_DSN']); s.repo_id='schema30-proof'
assert s.remote_schema_version is None or s.remote_schema_version==29
sprint=pg.create_sprint(s,'preserved','synthetic','2026-01-01','2099-12-31','active')
track=pg.get_or_create_track(s,sprint,'test'); item=pg.create_work_item(s,sprint,track,'preserved source')
rid='req_01JBC3D4E5F6G7H8J9K0MNPQRS'
packet={'schema':'operator-request/v1','kind':'perform','required_outcome':'verify','reason':'fixture',
 'execution':{'host':'test','directory':'/test','intended_identity':'test','target':'test','inputs':[]},
 'procedure':{'version':'1','steps':[{'step_id':'step','instruction':'check','expected_result':'ok','preconditions':[],
   'verification':'check','partial_progress':'record','recovery':'stop'}]},
 'blocker':{'condition':'test','checked_at':'2026-10-31T00:00:00Z','fresh_until':'2099-01-01T00:00:00Z','source_revisions':[]}}
a=c.publish_arguments({'request_id':rid,'packet':packet,'dependants':[{'work_item_id':item,'session_id':'test-session'}],'idempotency_key':'seed-publish'})
with s.conn.transaction(),s.conn.cursor() as cur:p.publish(cur,s,'fixture:publisher:0','fixture-workspace',a)
a=c.report_performed_arguments({'request_id':rid,'expected_revision':1,'outcome':'performed','note':'synthetic','idempotency_key':'seed-report'})
with s.conn.transaction(),s.conn.cursor() as cur:p.report_performed(cur,s,'fixture:publisher:0','fixture-workspace',a)
a=c.attest_arguments({'request_id':rid,'expected_revision':2,'statement':'synthetic','performed_at':'2026-10-31T00:00:00Z','idempotency_key':'seed-attest'})
with s.conn.transaction(),s.conn.cursor() as cur:p.attest(cur,s,'fixture:reviewer:0','fixture-workspace',a)
s.conn.commit(); s.conn.close(); print(json.dumps({'request_id':rid,'item_id':item}))
'''


def _validate_urls(migration: str, runtime: str) -> None:
    endpoints = []
    users = []
    for value in (migration, runtime):
        parsed = urlsplit(value)
        assert parsed.scheme == 'postgresql' and parsed.hostname in ('127.0.0.1','localhost','::1')
        assert parsed.port and parsed.path.startswith('/schema31_conformance_') and parsed.username
        assert not parsed.fragment and parse_qs(parsed.query,strict_parsing=True)=={'options':['-csearch_path=work,pg_catalog']}
        normalized = psycopg.conninfo.conninfo_to_dict(value)
        assert normalized['host']==parsed.hostname and normalized['dbname']==parsed.path[1:] and normalized['user']==parsed.username
        endpoints.append((parsed.hostname,parsed.port,parsed.path));users.append(parsed.username)
    assert endpoints[0]==endpoints[1] and users[0]!=users[1]


def _run(args, *, extra=None):
    env={k:v for k,v in os.environ.items() if not k.startswith('SPRINTCTL_') and not k.startswith('PG')}
    env.update(extra or {})
    return subprocess.run(args,env=env,capture_output=True,text=True,timeout=120)


@pytest.mark.parametrize('query', [
    'host=remote.example', 'hostaddr=192.0.2.1', 'dbname=production',
    'user=postgres', 'service=production', 'options=-csearch_path%3Dpublic',
    'options=-csearch_path%3Dwork%2Cpg_catalog&options=-csearch_path%3Dpublic',
])
def test_schema31_fixture_refuses_libpq_routing_overrides(query):
    migration='postgresql://migration:fixture@127.0.0.1:5432/schema31_conformance_test?'+query
    runtime='postgresql://runtime:fixture@127.0.0.1:5432/schema31_conformance_test?options=-csearch_path%3Dwork%2Cpg_catalog'
    with pytest.raises(AssertionError):_validate_urls(migration,runtime)


def _rows_and_acls(url):
    with psycopg.connect(url) as conn:
        tables=conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='work' ORDER BY tablename").fetchall()
        rows={}
        for (name,) in tables:
            if name in ('schema_version','operator_continuation_receipt'):continue
            data=conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t').format(sql.Identifier('work',name))).fetchall()
            rows[name]=sorted((x[0] for x in data),key=lambda x:json.dumps(x,sort_keys=True))
        acl=conn.execute("SELECT c.relname,c.relkind,c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='work' AND c.relkind IN ('r','S') ORDER BY c.relname").fetchall()
        functions=conn.execute("SELECT p.oid::regprocedure::text,p.proacl::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='work' ORDER BY 1").fetchall()
        schema_acl=conn.execute("SELECT nspacl::text FROM pg_namespace WHERE nspname='work'").fetchone()[0]
        return rows,{'relations':{x[0]:(x[1],x[2]) for x in acl},
                     'functions':dict(functions),'schema':schema_acl}


def test_published_29_to_31_explicit_migration_preserves_rows_and_acls():
    names=('VUORO_SCHEMA31_MIGRATION_DSN','VUORO_SCHEMA31_RUNTIME_DSN','VUORO_SCHEMA29_PYTHON','VUORO_SCHEMA29_CLI','VUORO_SCHEMA31_CLI','VUORO_SCHEMA31_OWNER_PREFIX')
    if not all(os.environ.get(k) for k in names):
        if os.environ.get('VUORO_SCHEMA31_PROOF')=='1':pytest.fail('required schema31 owner fixture missing')
        pytest.skip('requires separately provisioned disposable schema31 owner fixture')
    migration,runtime,old_python,old_cli,new_cli,new_prefix=(os.environ[k] for k in names)
    _validate_urls(migration,runtime)
    assert not any(k.startswith('PG') for k in os.environ), 'ambient libpq configuration is forbidden'
    for p in (old_python,old_cli,new_cli):assert Path(p).is_absolute() and Path(p).is_file()
    import sprintctl
    assert Path(sprintctl.__file__).resolve().is_relative_to(Path(new_prefix).resolve())
    assert sprintctl.__version__=='0.28.0'
    old_identity=_run([old_python,'-I','-c',"import sprintctl; print(sprintctl.__version__); print(sprintctl.__file__)"])
    assert old_identity.returncode==0 and old_identity.stdout.splitlines()[0]=='0.26.0'
    assert Path(old_identity.stdout.splitlines()[1]).resolve().is_relative_to(Path(old_python).parent.parent.resolve())
    for url in (migration,runtime):
        with psycopg.connect(url) as conn:
            assert not any(conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
            assert conn.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]=='schema31:disposable-conformance'
    def cli(binary,cmd,url):return _run([binary,'remote-schema',cmd,'--url',url,'--json'])
    setup=cli(old_cli,'migrate',migration);assert setup.returncode==0,setup.stderr
    assert json.loads(setup.stdout)['to_version']==29
    seed=_run([old_python,'-I','-c',OLD_SEED],extra={'MIGRATION_DSN':migration});assert seed.returncode==0,seed.stderr
    identity=json.loads(seed.stdout.strip().splitlines()[-1]); before,acls=_rows_and_acls(migration)
    assert before['operator_request'] and before['operator_request_event'] and before['work_item']
    old_before=cli(old_cli,'check',runtime)
    assert old_before.returncode==0 and json.loads(old_before.stdout)['compatible']
    check=cli(new_cli,'check',runtime)
    assert check.returncode!=0 and json.loads(check.stdout)['reason']=='schema-too-old'
    denied=cli(new_cli,'migrate',runtime)
    assert denied.returncode!=0 and 'permission denied' in denied.stderr.lower()
    assert _rows_and_acls(migration)==(before,acls)
    with psycopg.connect(migration) as conn:
        assert conn.execute('SELECT version FROM schema_version').fetchone()[0]==29
    migrated=cli(new_cli,'migrate',migration);assert migrated.returncode==0,migrated.stderr
    result=json.loads(migrated.stdout);assert result['from_version']==29 and result['to_version']==31 and result['applied_versions']==[30,31]
    replay=cli(new_cli,'migrate',migration);assert replay.returncode==0 and json.loads(replay.stdout)['applied_versions']==[]
    after,new_acls=_rows_and_acls(migration)
    assert after==before and new_acls['schema']==acls['schema']
    assert all(new_acls[kind].get(k)==v for kind in ('relations','functions') for k,v in acls[kind].items())
    old=cli(old_cli,'check',runtime);assert old.returncode!=0 and json.loads(old.stdout)['reason']=='schema-too-new'
    current=cli(new_cli,'check',runtime);assert current.returncode==0 and json.loads(current.stdout)['compatible']
    with psycopg.connect(runtime) as conn:
        definitions=conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='work.work_run_delegation'::regclass AND conname='work_run_delegation_operations_check'").fetchall()
        assert len(definitions)==1
        assert set(re.findall(r'work\.[a-z0-9.-]+',definitions[0][0])) == {
            'work.run.resolve-v1','work.run.predecessor-context-v1','work.session-note.write-v1',
            'work.evidence.append-v1','work.lease.currentness-v1'}
        assert conn.execute("SELECT has_table_privilege(current_user,'work.operator_continuation_receipt','INSERT')").fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):conn.execute('CREATE TABLE work.runtime_must_not_create(id int)')
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):conn.execute('ALTER TABLE work.operator_continuation_receipt ADD COLUMN unauthorized integer')
        conn.rollback()
    from sprintctl import pg,operator_request_pg
    with psycopg.connect(runtime,row_factory=psycopg.rows.dict_row) as conn:
        store=pg.PgStore(conn=conn,repo_id='schema30-proof')
        with conn.cursor() as cur:result=operator_request_pg.continuation(cur,store,'fixture-workspace',identity['request_id'])
    assert result['request_revision']==result['resolution']['event_revision']==3
    assert result['dependants'][0]['work_item_id']==identity['item_id']
    assert result['dependants'][0]['state']=='continuation-ready'
