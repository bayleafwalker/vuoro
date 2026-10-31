"""Disposable installed-owner currentness exercise for the actual composed service.

No owner test modules or owner source checkout are imported. This helper must be
called inside the explicitly guarded 29->31 composition fixture after migration.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
import uuid

from psycopg import sql


def exercise_currentness(client, application):
    from sprintctl import pg
    from sprintctl.application import ApplicationRejection

    store = application.store
    identity = SimpleNamespace(actor='fixture-currentness', environment='schema28-fixture',
        authorities=frozenset({'work:evidence', 'work:claim', 'work:read'}),
        principal_id='github:100:0', workspace_id='schema28-fixture', client_id=None, grant_id=None)
    context = SimpleNamespace(identity=identity, request_id='currentness-setup', basis_revision=None,
        catalog_revision='fixture', idempotency_requirement='not-allowed', idempotency_key=None)
    def invoke(operation, arguments):
        return application.invoke(operation, arguments, context)
    key = lambda: uuid.uuid4().hex
    sprint = pg.create_sprint(store, 'installed-currentness-'+key(), 'fixture', '2026-01-01', '2099-12-31', 'active')
    track = pg.get_or_create_track(store, sprint, 'fixture')
    item = pg.create_work_item(store, sprint, track, 'installed owner currentness')
    pg.update_work_item_description(store, item, 'Exact fixture requirements', actor='fixture')
    reservation = pg.reserve(store, item, actor='fixture', session_id=key(), role='execution')
    pg.release_reservation(store, reservation['id'])
    run = invoke('work.run.register-v1', {'harness_id':'fixture','harness_build':'fixture','model_id':'scripted',
        'recipe_id':'installed-wheel','observed_profile':{'instruction_digest':'sha256:'+'a'*64,'skill_digests':[]},
        'idempotency_key':key()})['run']['run_id']
    lease = invoke('work.lease.acquire-v1', {'item_id':item,'run_id':run,'idempotency_key':key()})['lease']
    basis = invoke('work.run.continuation-delta-v1', {'run_id':run,'item_id':item})['delta']['current']
    body = {'run_id':run, 'lease_id':lease['lease_id'], 'checkpoint':basis}
    alphabet='0123456789ABCDEFGHJKMNPQRSTVWXYZ'; raw=uuid.uuid4().int
    instance='ins_'+''.join(alphabet[(raw>>(5*i))&31] for i in range(26))
    declaration={'schema':'agent-declaration/v1','harness':{'name':'test','version':'1'},'adapter':None,
        'host_class':'workstation','interactivity':'interactive','accepted_kinds':['continue'],
        'channels':[{'channel':'mcp','wake_modes':['read-while-running']}]}
    invoke('work.agent.register-v1', {'instance_id':instance,'declaration':declaration,'idempotency_key':key()})
    issued=invoke('work.run.delegate-v1', {'run_id':run,'creator_instance_id':instance,
        'operations':['work.lease.currentness-v1'],'ttl_seconds':300,'idempotency_key':key()})
    token=issued['token']; resolved=application.resolve_run_delegation_digest(hashlib.sha256(token.encode('ascii')).hexdigest())
    assert resolved.authorities == frozenset({'work:read'})
    assert tuple(resolved.delegation.operations)==('work.lease.currentness-v1',)

    def snapshot():
        # Capture all business rows and lease heartbeats, not merely totals.
        tables=('work_idempotency_ledger','work_outcome_report','work_decision','work_lease','event','work_run_delegation')
        result={}
        with store.conn.cursor() as cur:
            for table in tables:
                cur.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t WHERE repo_id=%s').format(sql.Identifier(table)), (store.repo_id,))
                result[table]=sorted((x['row'] for x in cur.fetchall()),key=lambda x:json.dumps(x,sort_keys=True,default=str))
        store.conn.rollback()
        return result

    def http(arguments=body, operation='work.lease.currentness-v1', repo=None):
        return client.post('/api/invoke/v1', headers={'Authorization':'Bearer '+token,'X-Vuoro-Client-Protocol':'1'},
            json={'schema_version':'invocation/v1','request_id':key(),'operation':operation,'repo_id':repo or store.repo_id,'arguments':arguments})
    before=snapshot()
    assert http().status_code==200 # discarded response: repeat the read
    answer=http(); assert answer.status_code==200, answer.text
    facts=answer.json()['result']['currentness']
    assert facts['authorizes_effect'] is False and facts['checkpoint']==basis
    assert facts['delegation']['operations']==['work.lease.currentness-v1']
    assert snapshot()==before
    assert http({**body,'run_id':'run_'+'A'*26}).status_code==403
    assert http(repo='wrong-repository').status_code==403
    for op,args in [('work.run.resolve-v1',{'run_id':run}),('work.run.delegate-v1',{'run_id':run}),
                    ('work.session-note.write-v1',{'run_id':run,'note':'denied','idempotency_key':key()})]:
        assert http(args,op).status_code==403
    assert snapshot()==before
    pg.add_ref(store,item,'doc','https://example.invalid/installed-currentness')
    stale_before=snapshot(); stale=http(); assert stale.status_code==409 and stale.json()['error']['code']=='work-basis-stale'
    assert snapshot()==stale_before
    invoke('work.run.delegation-revoke-v1', {'delegation_id':issued['delegation_id'],'reason':'fixture','idempotency_key':key()})
    revoked_before=snapshot(); revoked=http();assert revoked.status_code==401 and revoked.json()['error']['code']=='credential-invalid'
    assert snapshot()==revoked_before
    # Owner revalidates even when a caller retains a once-valid resolved identity.
    delegated=SimpleNamespace(**{**vars(identity),'authorities':resolved.authorities,'delegation':resolved.delegation})
    cached=SimpleNamespace(**{**vars(context),'identity':delegated})
    try:
        application.invoke('work.lease.currentness-v1',body,cached)
    except ApplicationRejection as exc:
        assert exc.code=='credential-invalid'
    else:
        raise AssertionError('cached revoked delegation accepted')
    assert snapshot()==revoked_before
    return token
