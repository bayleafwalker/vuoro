import copy
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('guard',ROOT/'scripts/service103_catalog_guard.py')
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)

@pytest.fixture
def catalog():
    unchanged=[{'name':f'old-{i}', 'input_schema':{'type':'object','required':[str(i)]}} for i in range(144)]
    old_changed=[{'name':name,'input_schema':{'enum':['old']}} for name in sorted(guard.CHANGED)]
    changed=[{'name':name,'input_schema':{'enum':['old','work.lease.currentness-v1']}} for name in sorted(guard.CHANGED)]
    baseline={'operation_sha256':{x['name']:guard.digest(x) for x in unchanged+old_changed}}
    current={'name':guard.CURRENTNESS,'owning_domain':'work','execution_semantics':'read','required_authority':'work:read',
      'idempotency':'not-allowed','result_schema':{'properties':{'currentness':{'properties':{'authorizes_effect':{'const':False}}}}}}
    outcome={'name':guard.OUTCOME,'owning_domain':'work','execution_semantics':'write','required_authority':'work:claim',
      'idempotency':'not-allowed','input_schema':{'required':['checkpoint']}}
    review={'changed':{x['name']:{'old_sha256':baseline['operation_sha256'][x['name']],'new_sha256':guard.digest(x)} for x in changed},
      'added':{x['name']:guard.digest(x) for x in (current,outcome)}}
    return {'operations':unchanged+changed+[current,outcome]},baseline,review

def test_qualifies_exact_reviewed_delta(catalog):
    actual,baseline,review=catalog;assert guard.validate(actual,baseline,review)['unchanged']==144

@pytest.mark.parametrize('fault',['unchanged','reviewed-change-extra','delegation-extra','missing','duplicated','unexpected-addition',
                                  'currentness-authority','currentness-effect','outcome-authority','outcome-checkpoint','old-hash'])
def test_rejects_unreviewed_changes(catalog,fault):
    actual,baseline,review=copy.deepcopy(catalog)
    if fault=='unchanged':actual['operations'][0]['input_schema']['required']=[]
    if fault=='reviewed-change-extra':actual['operations'][144]['input_schema']['enum'].append('unexpected')
    if fault=='delegation-extra':next(x for x in actual['operations'] if x['name']=='work.run.delegate-v1')['input_schema']['enum'].append('work.run.delegate-v1')
    if fault=='missing':actual['operations'].pop(0)
    if fault=='duplicated':actual['operations'][0]=copy.deepcopy(actual['operations'][1])
    if fault=='unexpected-addition':actual['operations'][-1]['name']='other'
    if fault=='currentness-authority':actual['operations'][-2]['required_authority']='work:write'
    if fault=='currentness-effect':actual['operations'][-2]['result_schema']['properties']['currentness']['properties']['authorizes_effect']={'const':True}
    if fault=='outcome-authority':actual['operations'][-1]['required_authority']='work:read'
    if fault=='outcome-checkpoint':actual['operations'][-1]['input_schema']['required']=[]
    if fault=='old-hash':review['changed']['work.run.delegate-v1']['old_sha256']='0'*64
    with pytest.raises(ValueError):guard.validate(actual,baseline,review)
