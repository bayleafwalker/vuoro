"""Public optional-image qualification and alias effects are fail closed."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def closure():
    raw = (ROOT / 'packages/vuoro-service/composition/lane-adapter-pins.json').read_bytes()
    manifest = json.loads(raw)
    records = []
    for pin in manifest['release_locks']:
        record = {k: v for k, v in pin.items() if k != 'distribution_version'}
        record.update(expected_version=pin['distribution_version'], installed_version=pin['distribution_version'],
                      wheel_present=True, installed_files_sha256='a' * 64, installed_files_count=10)
        records.append(record)
    digest = hashlib.sha256(raw).hexdigest()
    receipt = {'schema_version': 'vuoro-installed-composition/v1', 'verified': True,
               'manifest_sha256': digest, 'distributions': records}
    installed = {p['distribution']: p['distribution_version'] for p in manifest['release_locks']}
    return [raw, digest, receipt, copy.deepcopy(receipt), installed, (3, 13, 1)]


def test_exact_optional_installed_closure_is_accepted(closure):
    result = module('validate_lane_service_image').validate(*closure)
    assert result['verified'] and result['installed_versions']['lanectl'] == '0.3.1'


@pytest.mark.parametrize('fault', ['python', 'manifest', 'default', 'missing', 'duplicate',
                                  'version', 'source', 'unverified', 'files', 'fresh'])
def test_incorrect_or_incomplete_installed_closure_is_refused(closure, fault):
    if fault == 'python':
        closure[5] = (3, 12, 8)
    elif fault == 'manifest':
        closure[0] += b' '
    elif fault == 'default':
        raw = (ROOT / 'packages/vuoro-service/composition/adapter-pins.json').read_bytes()
        closure[0:2] = [raw, hashlib.sha256(raw).hexdigest()]
    elif fault == 'missing':
        closure[2]['distributions'].pop()
    elif fault == 'duplicate':
        closure[2]['distributions'].append(closure[2]['distributions'][0])
    elif fault == 'version':
        closure[4]['lanectl'] = '0.0.0'
    elif fault == 'source':
        closure[2]['distributions'][-1]['source_revision'] = '0' * 40
    elif fault == 'unverified':
        closure[2]['verified'] = False
    elif fault == 'files':
        closure[2]['distributions'][-1]['installed_files_count'] = 0
    elif fault == 'fresh':
        closure[3]['distributions'][-1]['installed_files_sha256'] = 'b' * 64
    with pytest.raises(ValueError):
        module('validate_lane_service_image').validate(*closure)


class Commands:
    def __init__(self, fail=None, wrong_digest=False, index_fault=None):
        self.calls = []
        self.fail = fail
        self.wrong_digest = wrong_digest
        self.index_fault = index_fault

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.fail is not None and self.fail(argv):
            raise subprocess.CalledProcessError(1, argv)
        output = b''
        if argv[:3] == ['gh', 'attestation', 'verify']:
            output = b'verified'
        elif argv[:3] == ['python3', str(ROOT / 'scripts/validate_service_image.py'), 'workspace-names']:
            output = b'vuoro-service\nvuoro-evidence\nvuoro-mcp-edge\n'
        elif argv[:2] == ['docker', 'run'] and kwargs.get('input') == (ROOT / 'scripts/validate_lane_service_image.py').read_bytes():
            digest = hashlib.sha256((ROOT / 'packages/vuoro-service/composition/lane-adapter-pins.json').read_bytes()).hexdigest()
            output = json.dumps({'verified': True, 'manifest_sha256': digest}).encode()
        elif argv[:4] == ['docker', 'buildx', 'imagetools', 'inspect'] and argv[-1] == '--raw':
            image = {'digest': 'sha256:'+'1'*64, 'platform': {'architecture': 'amd64', 'os': 'linux'}}
            attest = {'digest': 'sha256:'+'2'*64, 'platform': {'architecture': 'unknown', 'os': 'unknown'},
                      'annotations': {'vnd.docker.reference.type': 'attestation-manifest',
                                      'vnd.docker.reference.digest': image['digest']}}
            if argv[-2].endswith('2'*64):
                predicates = ['https://spdx.dev/Document', 'https://slsa.dev/provenance/v1']
                if self.index_fault == 'sbom':
                    predicates.pop(0)
                value = {'layers': [{'annotations': {'in-toto.io/predicate-type': p}} for p in predicates]}
            else:
                value = {'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': [image, attest]}
                if self.index_fault == 'manifest':
                    value['mediaType'] = 'application/vnd.oci.image.manifest.v1+json'
                if self.index_fault == 'platform':
                    image['platform']['architecture'] = 'arm64'
                if self.index_fault == 'binding':
                    attest['annotations']['vnd.docker.reference.digest'] = 'sha256:'+'3'*64
            output = json.dumps(value).encode()
        elif argv[:4] == ['docker', 'buildx', 'imagetools', 'inspect']:
            output = ('sha256:' + ('b' if self.wrong_digest else 'a') * 64).encode()
        return subprocess.CompletedProcess(argv, 0, output, b'')


def publish(tmp_path, commands, **overrides):
    args = dict(repository='bayleafwalker/vuoro', digest='sha256:' + 'a' * 64,
                source_sha='c' * 40, ref='vuoro-service-v0.1.106',
                receipt_path=tmp_path / 'receipt.json', run=commands)
    args.update(overrides)
    return module('publish_lane_image').publish(**args)


def aliases(commands):
    return [a for a, _ in commands.calls if a[:4] == ['docker', 'buildx', 'imagetools', 'create']]


def test_qualified_index_is_used_for_every_run_and_both_lane_aliases(tmp_path):
    commands = Commands()
    result = publish(tmp_path, commands)
    reference = 'ghcr.io/bayleafwalker/vuoro-service@sha256:' + 'a' * 64
    assert result['complete'] and len(result['aliases']) == 2
    assert all(a['verified'] and a['digest'] == result['image'].split('@')[1] for a in result['aliases'])
    assert all(reference in argv for argv, _ in commands.calls if argv[:2] == ['docker', 'run'])
    assert all(a[-1] == reference and ':lane-' in a[-2] for a in aliases(commands))
    verify = commands.calls[0][0]
    assert verify[:4] == ['gh', 'attestation', 'verify', 'oci://' + reference]
    assert '--source-digest' in verify and '--signer-workflow' in verify
    first_alias = next(i for i, (a, _) in enumerate(commands.calls) if a in aliases(commands))
    assert first_alias > next(i for i, (a, _) in enumerate(commands.calls) if a[-2:] == ['mcp-serve', '--help'])


@pytest.mark.parametrize('phase', ['attestation', 'pull', 'verifier', 'pip', 'preview', 'mcp'])
def test_any_required_check_failure_prevents_all_alias_effects(tmp_path, phase):
    def fail(argv):
        return {'attestation': argv[0] == 'gh', 'pull': argv[:2] == ['docker', 'pull'],
                'verifier': argv[-1] == hashlib.sha256((ROOT / 'packages/vuoro-service/composition/lane-adapter-pins.json').read_bytes()).hexdigest(),
                'pip': argv[-2:] == ['pip', 'check'],
                'preview': argv[:2] == ['docker', 'run'] and argv[-1] == '-',
                'mcp': argv[-2:] == ['mcp-serve', '--help']}[phase]
    commands = Commands(fail=fail)
    with pytest.raises(subprocess.CalledProcessError):
        publish(tmp_path, commands)
    assert aliases(commands) == []
    assert json.loads((tmp_path / 'receipt.json').read_text())['complete'] is False


def test_alias_digest_mismatch_fails_with_partial_publication_record(tmp_path):
    commands = Commands(wrong_digest=True)
    with pytest.raises(ValueError, match='top-level digest'):
        publish(tmp_path, commands)
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    assert not receipt['complete'] and len(receipt['aliases']) == 1
    assert receipt['aliases'][0]['updated'] is True and receipt['aliases'][0]['verified'] is False
    assert len(aliases(commands)) == 1


def test_alias_transport_failure_records_possible_effect_without_claiming_success(tmp_path):
    commands = Commands(fail=lambda a: a[:4] == ['docker', 'buildx', 'imagetools', 'create'])
    with pytest.raises(subprocess.CalledProcessError):
        publish(tmp_path, commands)
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    assert not receipt['complete'] and receipt['aliases'][0]['updated'] is None


@pytest.mark.parametrize('change', [{'digest': 'latest'}, {'source_sha': 'main'}, {'ref': '../unsafe'},
                                   {'ref': 'sha-'+'c'*40}, {'ref': 'candidate-sha-'+'c'*40}, {'ref': 'other-branch'}])
def test_unbound_artifact_inputs_fail_before_any_command(tmp_path, change):
    commands = Commands()
    with pytest.raises(ValueError):
        publish(tmp_path, commands, **change)
    assert commands.calls == []


@pytest.mark.parametrize('fault', ['manifest', 'platform', 'binding', 'sbom'])
def test_unqualified_index_prevents_aliases(tmp_path, fault):
    commands = Commands(index_fault=fault)
    with pytest.raises(ValueError):
        publish(tmp_path, commands)
    assert aliases(commands) == []


def test_timeout_is_recorded_before_any_alias_effect(tmp_path):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 300)
    with pytest.raises(subprocess.TimeoutExpired):
        publish(tmp_path, timeout)
    receipt = json.loads((tmp_path/'receipt.json').read_text())
    assert receipt['checks'][-1]['timeout'] is True and receipt['aliases'] == []


@pytest.mark.parametrize('phase', ['second-create', 'inspect'])
def test_partial_alias_failures_remain_incomplete(tmp_path, phase):
    def fail(a):
        return (a[:4] == ['docker', 'buildx', 'imagetools', 'create'] and ':lane-sha-' in a[-2]
                if phase == 'second-create' else a[:4] == ['docker', 'buildx', 'imagetools', 'inspect'] and a[-1] != '--raw')
    commands = Commands(fail=fail)
    with pytest.raises(subprocess.CalledProcessError):
        publish(tmp_path, commands)
    receipt = json.loads((tmp_path/'receipt.json').read_text())
    assert not receipt['complete'] and receipt['aliases']
    if phase == 'second-create':
        assert receipt['aliases'][0]['verified'] and receipt['aliases'][1]['effect'] == 'unknown'


def test_optional_workflow_and_pr_ci_select_the_same_explicit_closure():
    workflow = yaml.safe_load((ROOT / '.github/workflows/publish-lane-service-image.yaml').read_text())
    steps = workflow['jobs']['publish-lane']['steps']
    build = next(s['with'] for s in steps if s.get('id') == 'push')
    assert build['platforms'] == 'linux/amd64' and build['push'] is True
    assert build['provenance'] == 'mode=max' and build['sbom'] is True
    assert ':lane-candidate-sha-' in build['tags']
    assert 'PYTHON_BASE_IMAGE=python:3.13-slim' in build['build-args']
    assert 'VUORO_COMPOSITION_FILE=packages/vuoro-service/composition/lane-adapter-pins.json' in build['build-args']
    assert workflow['concurrency']['cancel-in-progress'] is False
    attest = next(i for i, s in enumerate(steps) if s.get('uses') == 'actions/attest@v4')
    qualify = next(i for i, s in enumerate(steps) if 'scripts/publish_lane_image.py' in s.get('run', ''))
    assert attest < qualify and 'if' not in steps[qualify]
    assert steps[attest]['with']['subject-digest'] == '${{ steps.push.outputs.digest }}'
    ci = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']['lane-service-image']
    ci_build = next(s['with'] for s in ci['steps'] if s.get('uses') == 'docker/build-push-action@v7')
    assert ci_build['push'] is False and ci_build['build-args'] == build['build-args']
    checks = ci['steps'][-1]['run']
    for script in ['validate_lane_service_image.py', 'validate_service_image.py', 'validate_service_image_preview.py']:
        assert script in checks
