"""Private owner attempt facts through ordinary HTTP and released wheels."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
import uuid

from fastapi.testclient import TestClient
import jsonschema
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from .sprintctl_binding import SprintctlProvider
from .test_bound_proposal_http import assert_installed_composition

OPEN = 'work.effect.attempt-open-v1'
REDEEM = 'work.effect.attempt-redeem-v1'
SEAL = 'work.effect.attempt-seal-unused-v1'
REPORT = 'work.effect.attempt-report-applied-v1'
GET = 'work.effect.attempt-get-v1'
OPERATIONS = (OPEN, REDEEM, SEAL, REPORT, GET)
pytestmark = pytest.mark.essential_safety


def accepted(response):
    assert response.status_code == 200, response.json()
    assert response.json()['status'] == 'accepted'
    return response.json()['result']


def owner_rows(owner):
    tables = ('work_effect_attempt','work_effect_attempt_event','work_idempotency_ledger',
              'work_effect_intent','run','evidence_item','work_release','work_item')
    result = {}
    for table in tables:
        rows = owner.store.conn.execute(
            f'SELECT to_jsonb(t) AS row FROM {table} t WHERE repo_id=%s',
            (owner.store.repo_id,)).fetchall()
        result[table] = sorted((r['row'] for r in rows),key=lambda x:json.dumps(x,sort_keys=True))
    owner.store.conn.commit()
    return result


@pytest.fixture
def attempt_owner(request):
    url = request.config.getoption('--lease-pg-url')
    if url is None:
        pytest.skip('requires configured disposable PostgreSQL owner')
    if os.environ.get('VUORO_BOUND_COMPOSITION_PROOF') == '1':
        assert_installed_composition()
    from importlib.metadata import version
    from sprintctl.vuoro_adapter import catalog_operation_specs, register_work_catalog, WORK_OPERATION_CONTRACTS
    assert version('sprintctl') == '0.18.0'
    enabled = {x['name']:x for x in catalog_operation_specs(resource_schema_available=True)}
    disabled = {x['name']:x for x in catalog_operation_specs(resource_schema_available=False)}
    assert all(enabled[op] == disabled[op] for op in OPERATIONS)
    owner = SprintctlProvider(url)
    base = Identity(actor='fixture-proposer',environment='lease-conformance',
        authorities=frozenset({'work:read','work:write','work:evidence','work.effect.propose'}),
        principal_id='github:100:0',workspace_id='lease-conformance',client_id='client-A',
        grant_id='grant-A',repo_ids=frozenset({owner.store.repo_id}))
    applier = replace(base,actor='fixture-applier',principal_id='github:300:0',
                      authorities=frozenset({'work.effect.mark-applied'}))
    identities = {'proposer':base,'verifier':replace(base,actor='fixture-verifier',
        principal_id='github:200:0',authorities=frozenset({'work:read','work:write','work:evidence','work.effect.accept'})),
        'applier':applier,'ordinary':replace(applier,authorities=frozenset({'work:read','work:write','work:evidence'}))}
    for field,value in [('principal_id','github:400:0'),('workspace_id','other-workspace'),
                        ('client_id','other-client'),('grant_id','other-grant')]:
        identities[field] = replace(applier,**{field:value})
    contracts = {x.name:x for x in WORK_OPERATION_CONTRACTS}
    registry = CatalogRegistry();register_work_catalog(registry,owner.app)
    try:
        with TestClient(create_app(settings=ServiceSettings(environment_name='lease-conformance'),
            registry=registry,identity_resolver=StaticBearerIdentityResolver(identities))) as client:
            def invoke(op,args,*,token='applier',key=None,repo_id=None):
                envelope = {'schema_version':'invocation/v1','request_id':uuid.uuid4().hex,
                    'repo_id':repo_id or owner.store.repo_id,'catalog_revision':registry.revision,
                    'operation':op,'arguments':args}
                if key is not None:envelope['idempotency_key']=key
                response = client.post('/api/invoke/v1',json=envelope,
                    headers={'Authorization':'Bearer '+token,'X-Vuoro-Client-Protocol':'1'})
                if response.status_code == 200:
                    jsonschema.validate(response.json()['result'],contracts[op].result_schema)
                return response

            def run(token):
                return accepted(invoke('work.run.register-v1',{'harness_id':'conformance',
                    'harness_build':'test','model_id':'scripted','recipe_id':'attempt-http/v1',
                    'observed_profile':{'instruction_digest':'sha256:'+'a'*64,'skill_digests':[]},
                    'idempotency_key':uuid.uuid4().hex},token=token))['run']['run_id']

            item = int(owner.new_subject())
            revision = owner.pg.item_release_revision(owner.store,item)
            reserve = accepted(invoke('work.reservation.reserve-v1',{'item_id':item,
                'actor':'fixture-proposer','session_id':'attempt-http','expected_revision':revision,
                'acceptance_contract':{'effect_verification_required':True}},
                token='proposer',key=uuid.uuid4().hex))['reservation']
            intent = accepted(invoke('work.effect.propose-v1',{'run_id':run('proposer'),
                'item_id':item,'repository':'example','base_commit':'b'*40,'title':'Fixture typo',
                'rationale':'Correct fixture typo','unified_diff':'--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n',
                'idempotency_key':uuid.uuid4().hex},token='proposer'))['intent']
            verify_run = run('verifier')
            proof = {'schema':'sprintctl-protected-artifact-verification/v1',
                'intent_id':intent['intent_id'],'intent_revision':intent['revision'],
                'canonical_intent_digest':intent['canonical_intent_digest'],'release_digest':reserve['release_digest'],
                'artifact':{'domain':'utf8-unified-diff/v1','digest':'sha256:'+hashlib.sha256(intent['unified_diff'].encode()).hexdigest()},
                'checks':[{'name':'patch-check','revision':'sha256:'+'d'*64,'status':'passed'}]}
            proof_id = 'verification-'+uuid.uuid4().hex
            accepted(invoke('work.evidence.append-v1',{'run_id':verify_run,'item_id':proof_id,
                'kind':'protected-artifact-verification','ref':'fixture:protected-result',
                'digest':'sha256:'+hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
                'collector':'fixture-protected-verifier/v1',
                'validity':{'basis':'indefinite','valid_from':'2026-10-06T00:00:00Z','valid_until':None,'component_digests':{}},
                'claims':[{'claim_type':'observation','subject':intent['intent_id'],'grant_id':None,
                           'freshness':None,'confirms':None,'detail':proof}],
                'provenance':{},'chain_seq':0,'chain_prev_digest':None,'idempotency_key':uuid.uuid4().hex},token='verifier'))
            binding = {k:intent[k] for k in ('intent_id','revision','canonical_intent_digest')}
            intent = accepted(invoke('work.effect.accept-v1',{**binding,
                'verification_ref':{'run_id':verify_run,'item_id':proof_id}},token='verifier'))['intent']
            args = {**binding,'expected_revision':revision,'release_digest':reserve['release_digest'],
                'target':{'operation':'push_branch','branch':'fixture/'+intent['intent_id'],'commit_sha':'c'*40},
                'idempotency_key':uuid.uuid4().hex}
            yield owner,item,intent,args,invoke
    finally:
        owner.close()


def opened(fixture, *, pr=False):
    owner,item,intent,args,invoke=fixture
    args=deepcopy(args)
    if pr:
        args['target'].update(operation='open_pull_request',base_branch='main')
        args['idempotency_key']=uuid.uuid4().hex
    result=accepted(invoke(OPEN,args))
    consume={'attempt_id':result['authorization']['attempt_id'],
             'authorization_digest':result['authorization_digest'],'idempotency_key':uuid.uuid4().hex}
    assert result['authorization']['verification_binding']==intent['acceptance']['verification']
    assert result['authorization']['verification_binding'] is not None
    return result,consume


def test_pure_applier_bound_protected_facts_and_read_only_history(attempt_owner):
    owner,item,intent,args,invoke=attempt_owner
    result,consume=opened(attempt_owner)
    assert result['authorization']['principal_id']=='github:300:0'
    assert result['authorization']['workspace_id']=='lease-conformance'
    assert result['authorization']['client_id']=='client-A' and result['authorization']['grant_id']=='grant-A'
    assert result['authorization']['expected_revision']==args['expected_revision']
    assert result['authorization']['release_digest']==args['release_digest']
    assert result['authorization']['target']=={**args['target'],
        'repository':intent['repository'],'base_commit':intent['base_commit'],
        'title_sha256':hashlib.sha256(intent['title'].encode()).hexdigest(),
        'body_sha256':hashlib.sha256(intent['rationale'].encode()).hexdigest()}
    before=owner_rows(owner)
    history=accepted(invoke(GET,{'attempt_id':consume['attempt_id']}))
    assert history['state']=='accepted' and len(history['events'])==1
    assert owner_rows(owner)==before
    assert not [x for x in before['run'] if x['principal_id']=='github:300:0']
    assert history['authorization']['verification_binding']==intent['acceptance']['verification']
    opened(attempt_owner,pr=True)


def test_redemption_replay_never_regrants_after_basis_change(attempt_owner):
    owner,item,intent,args,invoke=attempt_owner
    _,consume=opened(attempt_owner)
    fresh=accepted(invoke(REDEEM,consume));assert fresh['dispatch_permitted'] is True
    before=owner_rows(owner)
    replay=accepted(invoke(REDEEM,consume));assert replay['dispatch_permitted'] is False
    assert replay['receipt']==fresh['receipt'] and replay['delivery']=='replay'
    assert owner_rows(owner)==before
    response=invoke(REDEEM,{**consume,'idempotency_key':uuid.uuid4().hex})
    assert response.status_code==409 and response.json()['error']['code']=='effect-attempt-already-consumed'
    assert owner_rows(owner)==before
    owner.pg.update_work_item_description(owner.store,item,'basis changed after redemption')
    replay=accepted(invoke(REDEEM,consume));assert replay['dispatch_permitted'] is False
    assert replay['receipt']==fresh['receipt']
    before=owner_rows(owner)
    response=invoke(REDEEM,{**consume,'idempotency_key':uuid.uuid4().hex})
    assert response.status_code==409 and response.json()['error']['code']=='effect-release-mismatch'
    assert owner_rows(owner)==before


@pytest.mark.parametrize('drift',[False,True])
def test_unused_seal_after_basis_change_refuses_later_redemption(attempt_owner,drift):
    owner,item,_,_,invoke=attempt_owner
    _,consume=opened(attempt_owner)
    if drift:owner.pg.update_work_item_description(owner.store,item,'basis changed')
    sealed=accepted(invoke(SEAL,consume));assert sealed['receipt']
    before=owner_rows(owner)
    response=invoke(REDEEM,{**consume,'idempotency_key':uuid.uuid4().hex})
    assert response.status_code==409 and response.json()['error']['code']==(
        'effect-release-mismatch' if drift else 'effect-attempt-already-consumed')
    assert owner_rows(owner)==before
    assert accepted(invoke(GET,{'attempt_id':consume['attempt_id']}))['state']=='sealed_unused'


def test_pr_report_is_exact_claim_and_does_not_apply_legacy_intent(attempt_owner):
    owner,_,intent,_,invoke=attempt_owner
    _,consume=opened(attempt_owner,pr=True)
    initial=owner_rows(owner)
    report={**consume,'commit_sha':'c'*40,'pr_url':'https://example.invalid/pulls/1'}
    before=owner_rows(owner)
    assert invoke(REPORT,report).status_code==409 and owner_rows(owner)==before
    accepted(invoke(REDEEM,consume))
    report['idempotency_key']=uuid.uuid4().hex
    first=accepted(invoke(REPORT,report));before=owner_rows(owner)
    assert accepted(invoke(REPORT,report))['receipt']==first['receipt']
    assert owner_rows(owner)==before
    assert before['work_effect_intent']==initial['work_effect_intent']
    assert before['evidence_item']==initial['evidence_item']
    history=accepted(invoke(GET,{'attempt_id':consume['attempt_id']}))
    assert [x['event_kind'] for x in history['events']]==[
        'attempt_authorization_accepted','invocation_authorization_redeemed','application_report_received']
    assert history['events'][-1]==first['receipt']
    assert history['events'][-1]['payload']=={'commit_sha':'c'*40,'pr_url':report['pr_url']}
    assert owner_rows(owner)==before
    bad={**report,'idempotency_key':uuid.uuid4().hex,'commit_sha':'d'*40}
    assert invoke(REPORT,bad).status_code==409 and owner_rows(owner)==before


def test_changed_target_same_key_and_competing_key_have_no_effects(attempt_owner):
    owner,_,_,args,invoke=attempt_owner
    opened(attempt_owner)
    before=owner_rows(owner)
    changed=deepcopy(args);changed['target']['commit_sha']='d'*40
    response=invoke(OPEN,changed)
    assert response.status_code==409 and response.json()['error']['code']=='idempotency-conflict'
    changed['idempotency_key']=uuid.uuid4().hex
    response=invoke(OPEN,changed)
    assert response.status_code==409 and response.json()['error']['code']=='effect-attempt-already-authorized'
    assert owner_rows(owner)==before


@pytest.mark.parametrize('op',OPERATIONS)
def test_all_private_operations_refuse_ordinary_authority_and_outer_key(attempt_owner,op):
    owner,_,_,args,invoke=attempt_owner
    _,consume=opened(attempt_owner,pr=True)
    inputs={OPEN:args,REDEEM:consume,SEAL:consume,
        REPORT:{**consume,'commit_sha':'c'*40,'pr_url':'https://example.invalid/pulls/1'},
        GET:{'attempt_id':consume['attempt_id']}}
    before=owner_rows(owner)
    response=invoke(op,inputs[op],token='ordinary')
    assert response.status_code==403 and response.json()['error']['code']=='authority-required'
    response=invoke(op,inputs[op],key='forbidden-outer')
    assert response.status_code==400 and response.json()['error']['code']=='idempotency-key-not-allowed'
    assert owner_rows(owner)==before


@pytest.mark.parametrize('token',['principal_id','workspace_id','client_id','grant_id'])
def test_full_binding_refuses_even_committed_replay_key(attempt_owner,token):
    owner,_,_,_,invoke=attempt_owner
    _,consume=opened(attempt_owner)
    accepted(invoke(REDEEM,consume));before=owner_rows(owner)
    for op,args in [(REDEEM,consume),(GET,{'attempt_id':consume['attempt_id']}),
                    (SEAL,{**consume,'idempotency_key':uuid.uuid4().hex})]:
        response=invoke(op,args,token=token)
        if op == REDEEM and token in ('client_id','grant_id'):
            assert response.status_code==409 and response.json()['error']['code']=='idempotency-conflict'
        else:
            assert response.status_code==404 and response.json()['error']['code']=='effect-attempt-not-found'
    assert owner_rows(owner)==before


def test_wrong_repo_closed_target_stale_basis_and_wrong_digest_are_no_effects(attempt_owner):
    owner,item,_,args,invoke=attempt_owner
    before=owner_rows(owner)
    assert invoke(OPEN,args,repo_id='foreign-repo').status_code==403
    bad=deepcopy(args);bad['target']['extra']='forbidden'
    assert invoke(OPEN,bad).status_code==422 and owner_rows(owner)==before
    _,consume=opened(attempt_owner)
    before=owner_rows(owner)
    wrong=invoke(REDEEM,{**consume,'authorization_digest':'0'*64})
    assert wrong.status_code==409 and wrong.json()['error']['code']=='effect-attempt-digest-mismatch'
    assert owner_rows(owner)==before
    owner.pg.update_work_item_description(owner.store,item,'same Release digest new full revision')
    before=owner_rows(owner)
    assert invoke(REDEEM,consume).status_code==409 and owner_rows(owner)==before
