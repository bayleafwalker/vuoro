"""Optional immutable composition of the released lanectl owner adapter."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from vuoro_service.composition import CompositionManifest, CompositionError, _compose_lane

ROOT = Path(__file__).parents[1]


def manifest():
    return json.loads((ROOT / 'composition/adapter-pins.json').read_text())


def lane_pin():
    return SimpleNamespace(adapter_module='lanectl.vuoro_adapter', register='register_lane_catalog',
        distribution='lanectl', api_version='v1', schema_version='1')


def test_existing_composition_stays_three_owners_and_lane_requires_a_release_pin(tmp_path):
    original = manifest()
    original_path = tmp_path / 'original.json'
    original_path.write_text(json.dumps(original))
    assert {p.domain for p in CompositionManifest.load(original_path).adapters} == {'work','audit','knowledge'}
    lock = dict(original['release_locks'][0], lock_id='lanectl', lock_kind='adapter',
        source_repository='https://github.com/bayleafwalker/lanectl', source_revision='a'*40,
        artifact_url='https://github.com/bayleafwalker/lanectl/releases/download/v0.1.0/lanectl-0.1.0-py3-none-any.whl',
        artifact_sha256='b'*64, distribution='lanectl', distribution_version='0.1.0')
    original['release_locks'].append(lock)
    original['runtime_descriptors'].append({'domain':'lane','lock_id':'lanectl','dependency_lock_ids':[],
        'adapter_module':'lanectl.vuoro_adapter','register':'register_lane_catalog','api_version':'v1','schema_version':'1'})
    path = tmp_path / 'lane.json'
    path.write_text(json.dumps(original))
    assert {p.domain for p in CompositionManifest.load(path).adapters} == {'work','audit','knowledge','lane'}
    original['runtime_descriptors'][-1]['domain'] = 'arbitrary'
    path.write_text(json.dumps(original))
    with pytest.raises(CompositionError):
        CompositionManifest.load(path)


def test_lane_runtime_uses_owner_readiness_and_never_migrates(monkeypatch):
    calls = []
    monkeypatch.setattr("vuoro_service.composition.sys", SimpleNamespace(version_info=(3, 13)))
    class Error(RuntimeError):
        pass
    def store(path):
        calls.append(path)
        return 'owner-store'
    monkeypatch.setitem(sys.modules, 'lanectl.stream_store', SimpleNamespace(StreamStore=store, StreamError=Error))
    monkeypatch.setattr('vuoro_service.composition._load_function', lambda pin: lambda registry, owner: calls.append((registry,owner)))
    state = _compose_lane(lane_pin(), 'catalog', {'VUORO_LANE_DATABASE':'/state/owner.sqlite'})
    assert state.state == 'compatible' and calls == [Path('/state/owner.sqlite'), ('catalog','owner-store')]
    def missing(path):
        raise Error('unmigrated')
    monkeypatch.setitem(sys.modules, 'lanectl.stream_store', SimpleNamespace(StreamStore=missing, StreamError=Error))
    with pytest.raises(CompositionError, match='migration separately'):
        _compose_lane(lane_pin(), 'catalog', {'VUORO_LANE_DATABASE':'/state/owner.sqlite'})


def test_lane_cannot_substitute_a_foreign_registration_or_schema():
    for field, value in [('adapter_module','foreign.adapter'),('register','migrate'),('schema_version','99')]:
        pin = lane_pin()
        setattr(pin, field, value)
        with pytest.raises(CompositionError, match='supported owner adapter'):
            _compose_lane(pin, None, {})


def test_lane_runtime_refuses_python312_before_importing_owner(monkeypatch):
    monkeypatch.setattr('vuoro_service.composition.sys',SimpleNamespace(version_info=(3,12)))
    with pytest.raises(CompositionError,match='Python >=3.13'):
        _compose_lane(lane_pin(),None,{'VUORO_LANE_DATABASE':'/state/owner.sqlite'})
