"""Exact service102 baseline plus the four and two reviewed service103 changes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / 'packages/vuoro-service/tests/fixtures'
BASELINE = FIXTURES / 'service102-catalog-baseline.json'
REVIEW = FIXTURES / 'service103-reviewed-catalog-delta.json'
CURRENTNESS = 'work.lease.currentness-v1'
OUTCOME = 'work.lease.report-outcome-v2'
CHANGED = frozenset({'work.run.continuation-delta-v1', 'work.run.delegate-v1',
                     'work.run.delegation-get-v1', 'work.run.delegation-revoke-v1'})
ADDED = frozenset({CURRENTNESS, OUTCOME})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(catalog, baseline, review):
    old = baseline['operation_sha256']; changes = review['changed']; added = review['added']
    entries = catalog['operations']
    if len(old) != 148 or len(entries) != 150:
        raise ValueError('catalog count mismatch')
    if set(changes) != CHANGED or set(added) != ADDED:
        raise ValueError('reviewed change inventory mismatch')
    actual = {x['name']: x for x in entries}
    if len(actual) != len(entries) or set(actual) != set(old) | ADDED or set(old) & ADDED:
        raise ValueError('catalog operation set mismatch')
    for name, expected in old.items():
        if name in CHANGED:
            pair=changes[name]
            if pair.get('old_sha256') != expected: raise ValueError('reviewed prior hash mismatch: '+name)
            expected=pair['new_sha256']
        if digest(actual[name]) != expected:
            raise ValueError('released descriptor changed outside reviewed hash: ' + name)
    for name in ADDED:
        if digest(actual[name]) != added[name]: raise ValueError('added descriptor differs from review: '+name)
    read=actual[CURRENTNESS]; write=actual[OUTCOME]
    if (read.get('owning_domain'), read.get('execution_semantics'), read.get('required_authority'), read.get('idempotency')) != ('work','read','work:read','not-allowed'):
        raise ValueError('currentness contract is not read-only')
    try:
        authority=read['result_schema']['properties']['currentness']['properties']['authorizes_effect']
        required=write['input_schema']['required']
    except (KeyError,TypeError): raise ValueError('missing currentness or checkpoint boundary') from None
    if authority != {'const': False}: raise ValueError('currentness may not authorize effects')
    if (write.get('owning_domain'),write.get('execution_semantics'),write.get('required_authority'),write.get('idempotency')) != ('work','write','work:claim','not-allowed') or 'checkpoint' not in required:
        raise ValueError('outcome scope or checkpoint differs')
    return {'old_count':148,'unchanged':144,'changed':4,'added':sorted(ADDED),'current_count':150}


def falsify_actual_catalog(catalog, baseline, review):
    """Prove the reviewed guard rejects changes to the measured production catalog."""
    import copy
    mutations=[]
    extra=copy.deepcopy(catalog);extra['operations'].append({'name':'work.unreviewed-v1'});mutations.append(extra)
    for name in sorted(CHANGED):
        altered=copy.deepcopy(catalog)
        next(x for x in altered['operations'] if x['name']==name)['execution_semantics']='unreviewed'
        mutations.append(altered)
    altered=copy.deepcopy(catalog)
    next(x for x in altered['operations'] if x['name']==CURRENTNESS)['result_schema']['properties']['currentness']['properties']['authorizes_effect']={'const':True}
    mutations.append(altered)
    altered=copy.deepcopy(catalog)
    next(x for x in altered['operations'] if x['name']==OUTCOME)['required_authority']='work:read';mutations.append(altered)
    altered=copy.deepcopy(catalog)
    next(x for x in altered['operations'] if x['name']==OUTCOME)['input_schema']['required'].remove('checkpoint');mutations.append(altered)
    altered=copy.deepcopy(catalog)
    next(x for x in altered['operations'] if x['name']=='work.run.delegate-v1')['input_schema']['properties']['operations']['items']['enum'].append('work.unreviewed-v1');mutations.append(altered)
    for changed in mutations:
        try:validate(changed,baseline,review)
        except ValueError:continue
        raise AssertionError('catalog mutation escaped the release guard')
    return len(mutations)


def main():
    import sys
    if len(sys.argv)!=2: raise SystemExit('usage: service103_catalog_guard.py <measured-catalog.json>')
    print(json.dumps(validate(json.loads(Path(sys.argv[1]).read_text()),json.loads(BASELINE.read_text()),json.loads(REVIEW.read_text())),sort_keys=True))

if __name__=='__main__': main()
