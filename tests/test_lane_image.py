"""Image packaging includes the immutable optional owner and checks its Python floor."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def manifest(lane=True):
    raw=json.loads((ROOT/'packages/vuoro-service/composition/adapter-pins.json').read_text())
    if lane:
        raw['release_locks'].append({'lock_id':'lanectl','lock_kind':'adapter',
            'source_repository':'https://github.com/bayleafwalker/lanectl','source_revision':'a'*40,
            'artifact_url':'https://github.com/bayleafwalker/lanectl/releases/download/v0.2.0/lanectl-0.2.0-py3-none-any.whl',
            'artifact_sha256':'b'*64,'distribution':'lanectl','distribution_version':'0.2.0'})
        raw['runtime_descriptors'].append({'domain':'lane','lock_id':'lanectl','dependency_lock_ids':[],
            'adapter_module':'lanectl.vuoro_adapter','register':'register_lane_catalog','api_version':'v1','schema_version':'1'})
    return raw


def test_fetch_and_attest_accept_exact_optional_pins_but_interpreter_floor_is_explicit():
    fetch=module('fetch_pinned_adapters')
    attest=module('attest_installed_composition')
    raw=manifest()
    assert 'lanectl' in {lock['distribution'] for _,lock in fetch.artifact_pins(raw)}
    assert 'lanectl' in {entry['distribution'] for entry in attest._pinned(raw)}
    with pytest.raises(SystemExit,match='Python >=3.13'):
        fetch.validate_interpreter(raw,(3,12))
    fetch.validate_interpreter(raw,(3,13))
    fetch.validate_interpreter(manifest(False),(3,12))
    raw['runtime_descriptors'][-1]['domain']='arbitrary'
    with pytest.raises(SystemExit):
        fetch.artifact_pins(raw)
    with pytest.raises(SystemExit):
        attest._pinned(raw)


def test_installer_installs_all_only_verified_wheels_and_refuses_tampering(tmp_path,monkeypatch):
    installer=module('install_pinned_adapters')
    raw=manifest()
    names=[]
    for lock in raw['release_locks']:
        wheel=tmp_path/lock['artifact_url'].rsplit('/',1)[-1]
        wheel.write_bytes(lock['distribution'].encode())
        lock['artifact_sha256']=hashlib.sha256(wheel.read_bytes()).hexdigest()
        names.append(str(wheel))
    path=tmp_path/'manifest.json'
    path.write_text(json.dumps(raw))
    helpers=installer.fetch_helpers()
    original_check=helpers['validate_interpreter']
    helpers['validate_interpreter']=lambda value: original_check(value,(3,13))
    monkeypatch.setattr(installer,'fetch_helpers',lambda: helpers)
    calls=[]
    installer.install(path,tmp_path,run=lambda argv,**kw:calls.append((argv,kw)))
    assert len(calls)==1 and calls[0][0][7:]==names
    assert calls[0][0][1:7]==['-m','pip','install','--no-cache-dir','--no-deps','--no-index']
    assert calls[0][1]=={'check':True}
    (tmp_path/'unlisted-1.0-py3-none-any.whl').write_bytes(b'unlisted')
    assert 'unlisted' not in ' '.join(calls[0][0])
    Path(names[-1]).write_bytes(b'tampered')
    calls.clear()
    with pytest.raises(SystemExit,match='checksum mismatch'):
        installer.install(path,tmp_path,run=lambda *a,**kw:calls.append(a))
    assert calls==[]


def test_image_base_and_composition_are_explicit_build_inputs():
    dockerfile=(ROOT/'Dockerfile').read_text()
    assert dockerfile.startswith('ARG PYTHON_BASE_IMAGE=python:3.12-slim\nFROM ${PYTHON_BASE_IMAGE}')
    assert 'ARG VUORO_COMPOSITION_FILE=packages/vuoro-service/composition/adapter-pins.json' in dockerfile
    assert 'COPY ${VUORO_COMPOSITION_FILE}' in dockerfile
    assert 'python /usr/local/bin/install-pinned-adapters' in dockerfile
