"""Disposable, actual-image adapter qualification; no production connection."""
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import asyncio
import copy
import importlib.metadata
import json
import os
import platform
import runpy
import shutil
import sqlite3
import threading

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_workspace import FsObjectStore
from vuoro_workspace.vuoro_adapter import KnowledgeApplication, register_knowledge_catalog
from vuoro_service.catalog import CatalogRegistry, OperationRejectedError
from vuoro_service.composition import CompositionManifest, CompositionError, _compose_lane
from vuoro_service.identity import Identity, InvocationContext
from lanectl.contracts import make_event
from lanectl.stream_store import initialize, StreamStore, StreamError

root = Path('/state')
manifest = CompositionManifest.load(Path('/opt/vuoro/composition/adapter-pins.json'))
pin = next(p for p in manifest.adapters if p.domain == 'lane')
checks = []


def checked(name):
    checks.append(name)
    print(json.dumps({'check': name, 'pass': True}), flush=True)


class WorkStub:
    # Only registration is exercised for the three existing owners. No claim
    # about their database compatibility or full composed HTTP startup follows.
    store = SimpleNamespace(remote_schema_version=34)

    @staticmethod
    def maintenance_resource_schema_available():
        return True


def no_connection():
    raise AssertionError('catalog registration unexpectedly opened a database')


registry = CatalogRegistry()
register_work_catalog(registry, WorkStub())
VuoroAuditAdapter(connection_factory=no_connection).register(registry)
register_knowledge_catalog(registry, KnowledgeApplication(
    no_connection, FsObjectStore(root / 'objects'), runtime_role='vuoro_knowledge_runtime'))
baseline = registry.catalog().model_dump(mode='json')
assert len(baseline['operations']) == 150
assert registry.revision == 'd9c1e0a8b20a5c26ab6b846506024384da6fcc345939a0f789b77c0b3d8c5ecb'
checked('actual pinned three-owner catalog registration, no database opened')

database = root / 'lane.sqlite'
try:
    _compose_lane(pin, registry, {'VUORO_LANE_DATABASE': str(database)})
except CompositionError as exc:
    assert 'migration separately' in str(exc)
else:
    raise AssertionError('unmigrated store admitted')
assert not database.exists() and registry.catalog().model_dump(mode='json') == baseline
checked('runtime refuses unmigrated owner without creating database or catalog entries')

for field, value in [('register', 'migrate'), ('schema_version', '99')]:
    try:
        _compose_lane(replace(pin, descriptor=replace(pin.descriptor, **{field: value})), registry, {'VUORO_LANE_DATABASE': str(database)})
    except CompositionError:
        pass
    else:
        raise AssertionError('foreign owner descriptor admitted')
checked('unsupported registration and schema refused')

installer = runpy.run_path('/usr/local/bin/install-pinned-adapters')
wheel_dir = root / 'adapters'
shutil.copytree('/opt/vuoro/adapters', wheel_dir)
manifest_path = Path('/opt/vuoro/composition/adapter-pins.json')
raw_manifest = json.loads(manifest_path.read_text())
(wheel_dir / 'unrelated.whl').write_bytes(b'unrelated fixture')
install_calls = []
installer['install'](manifest_path, wheel_dir, run=lambda argv, **kw: install_calls.append(argv))
assert len(install_calls) == 1 and len(install_calls[0][7:]) == 6
assert not any(path.endswith('unrelated.whl') for path in install_calls[0])
wheel = wheel_dir / 'lanectl-0.2.12-py3-none-any.whl'
original = wheel.read_bytes()
for fault in ('tampered', 'missing'):
    if fault == 'tampered':
        wheel.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    else:
        wheel.unlink()
    try:
        installer['install'](manifest_path, wheel_dir, run=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('install ran before verification')))
    except SystemExit as exc:
        assert 'checksum mismatch' in str(exc)
    else:
        raise AssertionError('unverified wheel admitted')
    wheel.write_bytes(original)
try:
    installer['fetch_helpers']()['validate_interpreter'](raw_manifest, (3, 12))
except SystemExit as exc:
    assert 'Python >=3.13' in str(exc)
else:
    raise AssertionError('Python3.12 floor guard accepted lane manifest')
checked('actual released-wheel tamper and missing-file refusals before install; exact six-pin selection; Python floor guard')

initialize(database)  # Explicit owner migration, deliberately before runtime.
state = _compose_lane(pin, registry, {'VUORO_LANE_DATABASE': str(database)})
assert state.state == 'compatible'
catalog = registry.catalog().model_dump(mode='json')


def catalog_oracle(value):
    before = {x['name']: x for x in baseline['operations']}
    after = {x['name']: x for x in value['operations']}
    assert len(after) == len(value['operations'])
    assert set(after) - set(before) == {'lane.stream.read-v1', 'lane.stream.append-v1'}
    assert all(after.get(name) == definition for name, definition in before.items())
    for name, authority, semantics in [('lane.stream.read-v1', 'work:read', 'read'),
                                       ('lane.stream.append-v1', 'work:evidence', 'write')]:
        assert after[name]['owning_domain'] == 'lane'
        assert after[name]['required_authority'] == authority
        assert after[name]['execution_semantics'] == semantics
        assert after[name]['repo_scoped'] is True


catalog_oracle(catalog)
assert len(catalog['operations']) == 152 and catalog['revision'] != baseline['revision']
for mutation in ('drop-existing', 'change-existing', 'extra-lane-operation', 'wrong-authority'):
    bad = copy.deepcopy(catalog)
    if mutation == 'drop-existing':
        bad['operations'] = bad['operations'][1:]
    elif mutation == 'change-existing':
        bad['operations'][0]['repo_scoped'] = not bad['operations'][0]['repo_scoped']
    elif mutation == 'extra-lane-operation':
        extra = copy.deepcopy(next(x for x in bad['operations'] if x['name'] == 'lane.stream.read-v1'))
        extra['name'] = 'lane.stream.delete-v1'
        bad['operations'].append(extra)
    else:
        next(x for x in bad['operations'] if x['name'] == 'lane.stream.read-v1')['required_authority'] = 'work:write'
    try:
        catalog_oracle(bad)
    except AssertionError:
        pass
    else:
        raise AssertionError('catalog oracle accepted ' + mutation)
checked('150 baseline definitions unchanged plus exact two scoped lane operations; four oracle falsifiers')

identity = Identity(actor='fixture-runner', environment='fixture',
                    authorities=frozenset({'work:read', 'work:evidence'}),
                    repo_ids=frozenset({'fixture-repo'}), workspace_id='fixture-workspace',
                    principal_id='fixture:runner:1')


def invoke(operation, arguments, *, who=identity, repo='fixture-repo'):
    context = InvocationContext(identity=who, request_id='fixture-request', basis_revision=None,
        catalog_revision=registry.revision, idempotency_requirement='not-allowed',
        idempotency_key=None, repo_id=repo)
    return asyncio.run(registry.invoke(registry.get(operation), arguments, context))


def rejected(code, operation, arguments, **kwargs):
    try:
        invoke(operation, arguments, **kwargs)
    except OperationRejectedError as exc:
        assert exc.code == code, (code, exc.code)
    else:
        raise AssertionError('expected owner refusal ' + code)


read, append = 'lane.stream.read-v1', 'lane.stream.append-v1'
event = make_event('release.published', {'release_id': 'fixture-release', 'revision': 1}, item_id='fixture-release')
args = {'stream': 'control', 'expected_revision': 0, 'events': [event], 'idempotency_key': 'fixture-batch-0001'}
assert invoke(read, {'stream': 'control'})['revision'] == 0
assert invoke(append, args) == {'previous_revision': 0, 'revision': 1, 'count': 1, 'replayed': False}
assert invoke(append, args)['replayed'] is True
changed = dict(args, events=[make_event('release.published', {'release_id': 'other', 'revision': 1}, item_id='other')])
rejected('idempotency-conflict', append, changed)
rejected('revision-conflict', append, dict(args, idempotency_key='fixture-batch-0002'))
rejected('revision-conflict', append, args, who=replace(identity, principal_id='fixture:other:1'))
assert invoke(read, {'stream': 'control'})['events'] == [event]
checked('actual registered owner append/read/replay and changed-payload/stale-revision refusals')

for who, repo in [(replace(identity, authorities=frozenset({'work:read'})), 'fixture-repo'),
                 (replace(identity, workspace_id=None), 'fixture-repo'),
                 (replace(identity, principal_id=None), 'fixture-repo'),
                 (identity, 'foreign-repo'), (identity, None)]:
    rejected('lane-scope-required', append, args, who=who, repo=repo)
rejected('lane-scope-required', read, {'stream': 'control'}, who=replace(identity, authorities=frozenset()))
assert invoke(read, {'stream': 'control'}, who=replace(identity, workspace_id='other-workspace'))['events'] == []
assert invoke(read, {'stream': 'other-stream'})['events'] == []
checked('missing authority/workspace/principal/repository refused; workspace and stream isolation')

rejected('invalid-event', append, dict(args, stream='invalid', events=[event, {'type': 'unknown'}]))
assert invoke(read, {'stream': 'invalid'})['revision'] == 0
assert invoke(read, {'stream': 'control'})['revision'] == 1
checked('invalid batch cannot partially append')

event2 = make_event('release.published', {'release_id': 'page-two', 'revision': 1}, item_id='page-two')
event3 = make_event('release.published', {'release_id': 'page-three', 'revision': 1}, item_id='page-three')
invoke(append, dict(args, stream='paging', events=[event, event2]))
page = invoke(read, {'stream': 'paging', 'limit': 1})
assert page['revision'] == 2 and page['has_more'] is True
invoke(append, dict(args, stream='paging', expected_revision=2, events=[event3], idempotency_key='paging-batch-0002'))
tail = invoke(read, {'stream': 'paging', 'after': page['next_after'], 'through': page['revision']})
assert tail['events'] == [event2] and tail['revision'] == 2 and not tail['has_more']
assert len(invoke(read, {'stream': 'paging', 'after': page['next_after']})['events']) == 2
checked('pagination retains snapshot through concurrent later append; unpinned control observes extra event')

barrier = threading.Barrier(2)


def contender(number):
    barrier.wait(timeout=5)
    try:
        return invoke(append, dict(args, stream='race', idempotency_key=f'race-batch-{number:04d}'),
                      who=replace(identity, principal_id=f'fixture:contender{number}:1'))['revision']
    except OperationRejectedError as exc:
        return exc.code


with ThreadPoolExecutor(max_workers=2) as pool:
    answers = list(pool.map(contender, (1, 2)))
assert set(answers) == {1, 'revision-conflict'}
assert len(invoke(read, {'stream': 'race'})['events']) == 1
checked('two concurrent connections at revision zero have one winner and one conflict')

backup, restore = root / 'backup.sqlite', root / 'restore.sqlite'
with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
    source.backup(target)
shutil.copyfile(backup, restore)
assert StreamStore(restore).read('fixture-workspace', 'fixture-repo', 'control')['events'] == [event]
initialize(database)
assert invoke(read, {'stream': 'control'})['events'] == [event]
bad = root / 'incompatible.sqlite'
shutil.copyfile(backup, bad)
with sqlite3.connect(bad) as connection:
    connection.execute('PRAGMA user_version=99')
try:
    StreamStore(bad)
except StreamError as exc:
    assert exc.code == 'incompatible-schema'
else:
    raise AssertionError('incompatible restore admitted')
checked('explicit migration idempotent; SQLite online backup and separate quiescent restore; incompatible schema refused')

result = {'complete': True, 'python': platform.python_version(), 'uid': os.getuid(),
    'checks': checks, 'check_count': len(checks), 'baseline_catalog_revision': baseline['revision'],
    'catalog_revision': catalog['revision'], 'operation_count': len(catalog['operations']),
    'installed': {name: importlib.metadata.version(name) for name in
        ('lanectl', 'vuoro-service', 'sprintctl', 'auditctl', 'vuoro-workspace')},
    'installed_attestation': json.loads(Path('/opt/vuoro/composition/installed-composition.json').read_text()),
    'scope': 'actual image, synthetic verified-context inputs and disposable SQLite; three-owner registration-only fixtures; no full HTTP boot, real identity, persistent hosting or production durability proof'}
print('QUALIFICATION_RESULT=' + json.dumps(result, sort_keys=True), flush=True)
