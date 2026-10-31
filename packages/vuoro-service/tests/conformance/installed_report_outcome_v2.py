"""Diagnostic for the additional published 0.28 write; no owner-test imports."""
from __future__ import annotations
from types import SimpleNamespace
import json
import uuid
from psycopg import sql


def exercise_report_outcome_v2(client, application, revoked_token):
    from sprintctl import pg
    store=application.store
    ident=SimpleNamespace(actor='fixture-claim',environment='schema28-fixture',authorities=frozenset({'work:read','work:claim','work:evidence'}),principal_id='github:100:0',workspace_id='schema28-fixture',client_id=None,grant_id=None)
    context=SimpleNamespace(identity=ident,request_id='fixture',basis_revision=None,catalog_revision='fixture',idempotency_requirement='not-allowed',idempotency_key=None)
    key=lambda:uuid.uuid4().hex
    def inv(op,args):return application.invoke(op,args,context)
    def make():
        sprint=pg.create_sprint(store,'outcome-'+key(),'fixture','2026-01-01','2099-12-31','active')
        track=pg.get_or_create_track(store,sprint,'fixture');item=pg.create_work_item(store,sprint,track,'installed v2 diagnostic')
        reservation=pg.reserve(store,item,actor='fixture',session_id=key(),role='execution');pg.release_reservation(store,reservation['id'])
        run=inv('work.run.register-v1',{'harness_id':'fixture','harness_build':'fixture','model_id':'scripted','recipe_id':'installed-wheel',
          'observed_profile':{'instruction_digest':'sha256:'+'a'*64,'skill_digests':[]},'idempotency_key':key()})['run']['run_id']
        lease=inv('work.lease.acquire-v1',{'item_id':item,'run_id':run,'idempotency_key':key()})['lease']
        checkpoint=inv('work.run.continuation-delta-v1',{'run_id':run,'item_id':item})['delta']['current']
        body={'lease_id':lease['lease_id'],'run_id':run,'outcome':'succeeded','summary':'fixture',
              'payload':{'candidate':'fixture'},'checks':[{'name':'tests','status':'passed'}],
              'idempotency_key':key(),'checkpoint':checkpoint}
        return item,body
    def invoke(body,token='c'*40,repo='agentops'):
        return client.post('/api/invoke/v1',headers={'Authorization':'Bearer '+token,'X-Vuoro-Client-Protocol':'1'},json={
          'schema_version':'invocation/v1','request_id':key(),'operation':'work.lease.report-outcome-v2','repo_id':repo,'arguments':body})
    def outcome(item):
        row=inv('work.lease.read-v1',{'item_id':item})
        return row['outcome_reports'],pg.list_decisions(store,item),pg.get_work_item(store,item)['status']
    def snapshot():
        result={}
        with store.conn.cursor() as cur:
            for table in ('work_idempotency_ledger','work_outcome_report','work_decision','work_lease','event'):
                cur.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t WHERE repo_id=%s').format(sql.Identifier(table)),(store.repo_id,))
                result[table]=sorted((x['row'] for x in cur.fetchall()),key=lambda x:json.dumps(x,sort_keys=True,default=str))
        store.conn.rollback()
        return result
    item,body=make();before=outcome(item);all_before=snapshot()
    assert invoke(body,token='r'*40).status_code==403 and outcome(item)==before and snapshot()==all_before
    assert invoke(body,repo='another-repository').status_code==403 and outcome(item)==before and snapshot()==all_before
    assert invoke(body,token=revoked_token).status_code==401 and outcome(item)==before and snapshot()==all_before
    first=invoke(body);assert first.status_code==200,first.text
    committed=snapshot()
    replay=invoke(body);assert replay.status_code==200 and replay.json()['result']==first.json()['result'] and snapshot()==committed
    reports,decisions,status=outcome(item);assert len(reports)==1 and len(decisions)==1 and status=='done'
    changed={**body,'summary':'changed idempotent meaning'}
    assert invoke(changed).status_code==409 and outcome(item)==(reports,decisions,status) and snapshot()==committed
    stale_item,stale_body=make();pg.add_ref(store,stale_item,'doc','https://example.invalid/stale-fixture')
    stale_before=snapshot();denied=invoke(stale_body);assert denied.status_code==409,denied.text
    stale_after=snapshot()
    reports,decisions,status=outcome(stale_item)
    assert len(reports)==1 and reports[0]['disposition']=='rejected' and reports[0]['decision_id'] is None and not decisions and status!='done'
    expired_item,expired_body=make()
    with store.conn.cursor() as cur:
        cur.execute("UPDATE work_lease SET heartbeat_at=clock_timestamp()-interval '2 hours' WHERE repo_id=%s AND lease_id=%s",(store.repo_id,expired_body['lease_id']))
    store.conn.commit();before=outcome(expired_item);expired_before=snapshot();expired=invoke(expired_body)
    assert expired.status_code in (403,409),expired.text
    after=outcome(expired_item);assert not after[1] and after[2]!='done' and len(after[0])==1 and after[0][0]['disposition']=='rejected' and after[0][0]['decision_id'] is None
    expired_after=snapshot()
    # Retain the legacy v1 behavior: changed reference does not require a v2 checkpoint.
    v1_item,v1_body=make();pg.add_ref(store,v1_item,'doc','https://example.invalid/v1-compat')
    v1_body.pop('checkpoint');v1=client.post('/api/invoke/v1',headers={'Authorization':'Bearer '+'c'*40,'X-Vuoro-Client-Protocol':'1'},json={
      'schema_version':'invocation/v1','request_id':key(),'operation':'work.lease.report-outcome-v1','repo_id':store.repo_id,'arguments':v1_body})
    assert v1.status_code==200 and v1.json()['result']['settled'] is True,v1.text
    counts=lambda before,after:{name:len(after[name])-len(before[name]) for name in before}
    def lease_change(before,after,lease_id):
        left=next(x for x in before['work_lease'] if x['lease_id']==lease_id)
        right=next(x for x in after['work_lease'] if x['lease_id']==lease_id)
        changed=sorted(k for k in set(left)|set(right) if left.get(k)!=right.get(k))
        return {'changed_fields':changed,'state_before':left['state'],'state_after':right['state'],
                'end_reason_after':right.get('end_reason')}
    stale_lease=lease_change(stale_before,stale_after,stale_body['lease_id'])
    expired_lease=lease_change(expired_before,expired_after,expired_body['lease_id'])
    expected_deltas={'work_idempotency_ledger':1,'work_outcome_report':1,'work_decision':0,'work_lease':0,'event':1}
    assert counts(stale_before,stale_after)==expected_deltas
    assert counts(expired_before,expired_after)==expected_deltas
    assert stale_lease=={'changed_fields':['end_reason','ended_at','state'],'state_before':'active','state_after':'released','end_reason_after':'work-basis-stale'}
    assert expired_lease=={'changed_fields':[],'state_before':'active','state_after':'active','end_reason_after':None}
    return {'valid_and_replay':'pass','authority_repo_and_revoked':'pass','stale_rejected_reports':len(reports),'expired_reports_before':len(before[0]),'expired_reports_after':len(after[0]),'expired_reason':after[0][0]['reason_code'],'stale_row_deltas':counts(stale_before,stale_after),'expired_row_deltas':counts(expired_before,expired_after),'v1_reference_change':'pass','stale_lease':stale_lease,'expired_lease':expired_lease}
