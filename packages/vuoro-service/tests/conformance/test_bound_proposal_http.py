"""Released causal owner through the production HTTP shell; disposable state only."""
import copy
from dataclasses import replace
import importlib
from importlib.metadata import version
import os
import uuid

from fastapi.testclient import TestClient
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from .sprintctl_binding import SprintctlProvider

BOUND = 'work.effect.propose-bound-v1'


def assert_installed_composition():
    expected = {'vuoro-service': ('vuoro_service','0.1.89'),
                'sprintctl': ('sprintctl','0.15.1'),
                'vuoro-adapter-kit': ('vuoro_adapter_kit','0.2.0'),
                'vuoro-schema-runtime': ('vuoro_schema_runtime','0.1.0'),
                'auditctl': ('auditctl','0.1.9')}
    for distribution,(module,release) in expected.items():
        assert version(distribution) == release
        assert '/site-packages/' in importlib.import_module(module).__file__


@pytest.fixture
def bound_owner(request):
    url = request.config.getoption('--lease-pg-url')
    if url is None: pytest.skip('requires configured disposable PostgreSQL owner')
    if os.environ.get('VUORO_BOUND_COMPOSITION_PROOF') == '1': assert_installed_composition()
    assert version('sprintctl') == '0.15.1'
    from sprintctl.vuoro_adapter import register_work_catalog
    owner = SprintctlProvider(url)
    identity = Identity(actor='A', environment='lease-conformance',
        authorities=frozenset({'work:read','work:write','work:evidence','work.effect.propose','work.effect.reject'}),
        principal_id='github:100:0',workspace_id='lease-conformance',
        client_id='client-A',grant_id='grant-A',repo_ids=frozenset({owner.store.repo_id}))
    identities = {'bound':identity,'ordinary':replace(identity,authorities=frozenset({'work:read','work:write','work:evidence'}))}
    for field,value in [('principal_id','github:200:0'),('workspace_id','foreign-workspace'),('client_id','client-B'),('grant_id','grant-B')]:
        identities[field] = replace(identity,**{field:value})
    registry = CatalogRegistry(); register_work_catalog(registry,owner.app)
    try:
        with TestClient(create_app(settings=ServiceSettings(environment_name='lease-conformance'),
            registry=registry,identity_resolver=StaticBearerIdentityResolver(identities))) as client:
            def invoke(op,arguments,*,token='bound',key=None,repo_id=None):
                envelope={'schema_version':'invocation/v1','request_id':uuid.uuid4().hex,
                    'repo_id':repo_id or owner.store.repo_id,'catalog_revision':registry.revision,
                    'operation':op,'arguments':arguments}
                if key is not None: envelope['idempotency_key']=key
                return client.post('/api/invoke/v1',headers={'Authorization':'Bearer '+token,'X-Vuoro-Client-Protocol':'1'},json=envelope)
            item = int(owner.new_subject()); revision=owner.pg.item_release_revision(owner.store,item)
            response=invoke('work.run.register-v1',{'harness_id':'conformance','harness_build':'test','model_id':'scripted',
                'recipe_id':'bound-http/v1','observed_profile':{'instruction_digest':'sha256:'+'a'*64,'skill_digests':[]},
                'idempotency_key':'bound-http-run-key'})
            assert response.status_code==200,response.json(); run=response.json()['result']['run']['run_id']
            response=invoke('work.reservation.reserve-v1',{'item_id':item,'actor':'A','session_id':'bound-http',
                'expected_revision':revision},key='bound-http-reserve-key')
            assert response.status_code==200,response.json(); reserve=response.json()['result']['reservation']
            digest=reserve['release_digest']
            # Explicit precommitted trailer relation fixture; admission must read
            # it rather than perform ingestion. Full ingestion follows separately.
            with owner.store.conn.cursor() as cur:
                cur.execute('INSERT INTO release_commit(repo_id,release_digest,commit_sha) VALUES (%s,%s,%s)',
                            (owner.store.repo_id,digest,'c'*40))
            owner.store.conn.commit()
            evidence={'run_id':run,'item_id':'bound-http-proof','kind':'test','ref':'local:test','digest':'sha256:'+'a'*64,
                'collector':'bound-http','validity':{'basis':'indefinite','valid_from':'2026-10-09T00:00:00Z','valid_until':None,'component_digests':{}},
                'claims':[],'provenance':{},'chain_seq':0,'chain_prev_digest':None,'idempotency_key':'bound-http-evidence-key'}
            response=invoke('work.evidence.append-v1',evidence)
            assert response.status_code==200,response.json()
            tail=owner.pg.evidence_tail(owner.store,run)
            args={'run_id':run,'item_id':item,'repository':'example','base_commit':'b'*40,'title':'Typo',
                'rationale':'Correct the typo','unified_diff':'--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n',
                'idempotency_key':'bound-http-proposal-key','causal_basis':{'expected_revision':revision,'release_digest':digest,
                    'reserve_idempotency_key':'bound-http-reserve-key','commit_sha':'c'*40,
                    'evidence_tail':{'item_id':tail['item_id'],'chain_seq':tail['chain_seq'],'entry_digest':owner.pg.evidence_entry_digest(tail)}}}
            yield owner,args,evidence,reserve,invoke
    finally: owner.close()


def counts(owner):
    with owner.store.conn.cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM work_effect_intent WHERE repo_id=%s) AS intents,"
                    "(SELECT count(*) FROM work_idempotency_ledger WHERE repo_id=%s AND tool='propose_effect') AS keys",(owner.store.repo_id,)*2)
        return dict(cur.fetchone())


@pytest.mark.essential_safety
def test_bound_http_exact_replay_after_current_state_changes(bound_owner):
    owner,args,evidence,reserve,invoke=bound_owner
    first=invoke(BOUND,args); assert first.status_code==200,first.json()
    original=first.json()['result']; intent=original['intent']
    resolved=invoke('work.run.resolve-v1',{'run_id':args['run_id']})
    assert resolved.status_code==200,resolved.json()
    assert original['admission']['causal_basis']==args['causal_basis']
    assert original['admission']['run_binding']==resolved.json()['result']
    assert original['admission']['reservation_id']==reserve['id']
    assert intent['release_digest']==args['causal_basis']['release_digest']
    assert invoke(BOUND,args).json()['result']==original
    rejected=invoke('work.effect.reject-v1',{'intent_id':intent['intent_id'],'revision':intent['revision'],
        'canonical_intent_digest':intent['canonical_intent_digest'],'reason':'test'})
    assert rejected.status_code==200,rejected.json()
    owner.pg.update_work_item_description(owner.store,args['item_id'],'changed after admission')
    later={**evidence,'item_id':'bound-http-later','chain_seq':1,'chain_prev_digest':args['causal_basis']['evidence_tail']['entry_digest'],
           'idempotency_key':'bound-http-later-key'}
    assert invoke('work.evidence.append-v1',later).status_code==200
    owner.pg.release_reservation(owner.store,reserve['id'],actor='A')
    replay=invoke(BOUND,args); assert replay.status_code==200,replay.json()
    result=replay.json()['result']; assert result['admission']==original['admission']
    assert result['intent']['state']=='rejected'
    assert counts(owner)=={'intents':1,'keys':1}


@pytest.mark.essential_safety
@pytest.mark.parametrize('token,code', [('ordinary','authority-required'),('principal_id','run-not-found'),
    ('workspace_id','run-not-found'),('client_id','run-not-found'),('grant_id','run-not-found')])
def test_bound_http_authority_and_full_binding_refused(bound_owner,token,code):
    owner,args,_,_,invoke=bound_owner
    response=invoke(BOUND,args,token=token)
    assert response.status_code in {403,404}; assert response.json()['error']['code']==code
    assert counts(owner)=={'intents':0,'keys':0}


@pytest.mark.essential_safety
@pytest.mark.parametrize('field,value,code', [('reserve_idempotency_key','missing-reserve-key','effect-causal-reserve-missing'),
    ('commit_sha','d'*40,'effect-causal-commit-unbound'),('release_digest','f'*64,'effect-causal-release-mismatch'),
    ('evidence_tail',{'item_id':'wrong','chain_seq':0,'entry_digest':'sha256:'+'a'*64},'effect-causal-evidence-head-mismatch')])
def test_bound_http_causal_refusal_has_zero_effects(bound_owner,field,value,code):
    owner,args,_,_,invoke=bound_owner; changed=copy.deepcopy(args);changed['causal_basis'][field]=value
    response=invoke(BOUND,changed)
    assert response.status_code==409,response.json(); assert response.json()['error']['code']==code
    assert counts(owner)=={'intents':0,'keys':0}


@pytest.mark.essential_safety
def test_bound_http_envelope_key_and_foreign_repository_refused(bound_owner):
    owner,args,_,_,invoke=bound_owner
    keyed=invoke(BOUND,args,key='forbidden-envelope-key')
    assert keyed.status_code==400;assert keyed.json()['error']['code']=='idempotency-key-not-allowed'
    foreign=invoke(BOUND,args,repo_id='foreign-repository')
    assert foreign.status_code==403;assert foreign.json()['error']['code']=='repo-unauthorized'
    assert counts(owner)=={'intents':0,'keys':0}


@pytest.mark.essential_safety
def test_bound_http_stale_fullrevision_and_legacy_collision(bound_owner):
    owner,args,_,_,invoke=bound_owner
    legacy={k:v for k,v in args.items() if k!='causal_basis'}
    assert invoke('work.effect.propose-v1',legacy).status_code==200
    conflict=invoke(BOUND,args)
    assert conflict.status_code==409;assert conflict.json()['error']['code']=='idempotency-conflict'
    changed=copy.deepcopy(args); changed['idempotency_key']='new-bound-proposal-key'
    owner.pg.update_work_item_description(owner.store,args['item_id'],'before admission')
    stale=invoke(BOUND,changed)
    assert stale.status_code==409;assert stale.json()['error']['code']=='effect-causal-stale-revision'
    assert counts(owner)=={'intents':1,'keys':1}


@pytest.mark.essential_safety
def test_bound_http_append_first_refuses_captured_tail(bound_owner):
    owner,args,evidence,_,invoke=bound_owner
    later={**evidence,'item_id':'bound-http-later','chain_seq':1,'chain_prev_digest':args['causal_basis']['evidence_tail']['entry_digest'],
           'idempotency_key':'bound-http-later-key'}
    assert invoke('work.evidence.append-v1',later).status_code==200
    stale=invoke(BOUND,args)
    assert stale.status_code==409;assert stale.json()['error']['code']=='effect-causal-evidence-head-mismatch'
    assert counts(owner)=={'intents':0,'keys':0}
