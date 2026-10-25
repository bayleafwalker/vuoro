"""Independent proposed F1a regressions. No product-source mutation.

Run with the prepared worktree interpreter. Tests mutate disposable copies only.
The two projection tests intentionally freeze rejection of unsupported semantic
OpenAPI members; root owns whether to admit/replace that policy before dispatch.
"""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import pytest
from vuoro_contracts import ContractBundle, ContractError, validate_bundle

WORKTREE = Path(__file__).resolve().parents[3]
FIXTURE = WORKTREE / 'packages/vuoro-contracts/tests/fixtures/probe'
spec = importlib.util.spec_from_file_location('reviewed_generator', WORKTREE / 'scripts/contracts/generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def inputs():
    manifest = json.loads((FIXTURE / 'operation-manifest.json').read_bytes())
    return (
        json.loads((FIXTURE / 'openapi/probe.openapi.json').read_bytes()),
        json.loads((FIXTURE / 'schema/probe.schema.json').read_bytes()),
        dict(contract_id=manifest['contractId'], contract_version=manifest['contractVersion'],
             source_sha256=manifest['provenance']['sourceSha256'],
             toolchain_sha256=manifest['provenance']['toolchainSha256'],
             exposures={name: op['exposure'] for name, op in manifest['operations'].items()},
             byte_bounds={name: {'requestMaxBytes': op['requestMaxBytes'],
                                 'responseMaxBytes': op['responseMaxBytes']}
                          for name, op in manifest['operations'].items()}),
    )


def test_annotation_ref_data_does_not_create_schema_edges():
    _, bundle, _ = inputs()
    validate_bundle(bundle)
    bundle['$defs']['Input']['examples'] = [
        {'$ref': 'Input.json'}, {'nested': {'$ref': 'not-a-schema-target.json'}}
    ]
    before = deepcopy(bundle)
    assert validate_bundle(bundle) is None
    assert bundle == before


def test_inherited_parameter_boundaries_are_not_silently_omitted():
    openapi, bundle, metadata = inputs()
    assert generator.operation_manifest(openapi, bundle, **metadata)['operations']['submit']['parameters']
    path = openapi['paths']['/probe/{key}']
    path['parameters'] = deepcopy(path['post']['parameters'])
    for method in ('get', 'post'):
        path[method].pop('parameters')
    # Reject until inherited parameter semantics are separately admitted/frozen.
    with pytest.raises(ContractError):
        generator.operation_manifest(openapi, bundle, **metadata)


def test_response_object_ref_is_not_an_absent_body():
    openapi, bundle, metadata = inputs()
    operation = openapi['paths']['/probe/{key}']['post']
    openapi['components']['responses'] = {'Down': deepcopy(operation['responses']['503'])}
    operation['responses']['503'] = {'$ref': '#/components/responses/Down'}
    metadata['byte_bounds']['submit']['responseMaxBytes']['503'] = None
    # Direct named schema boundaries are qualified; response-object refs are not.
    with pytest.raises(ContractError):
        generator.operation_manifest(openapi, bundle, **metadata)


@pytest.mark.parametrize('artifact', ['manifest', 'schema'])
def test_parsed_document_bytes_are_bound_to_authenticated_inventory(tmp_path, monkeypatch, artifact):
    root = tmp_path / 'bundle'
    shutil.copytree(FIXTURE, root)
    external_digest = sha256((root / 'integrity.json').read_bytes()).hexdigest()
    positive = ContractBundle.load(root, integrity_sha256=external_digest)
    assert positive.validate_response('submit', 503, {'code': 'unavailable'}) == {'code': 'unavailable'}
    if artifact == 'manifest':
        target = root / 'operation-manifest.json'
        original_bytes = target.read_bytes()
        changed = json.loads(original_bytes)
        changed['operations']['submit']['responses']['503'] = 'Result.json'
        target.write_text(json.dumps(changed))
        replacement = original_bytes
        status, invalid_value = 503, {'state': 'ready', 'revision': '0'}
    else:
        target = root / 'schema/probe.schema.json'
        original_bytes = target.read_bytes()
        changed = json.loads(original_bytes)
        changed['$defs']['Result']['properties']['state'] = {'const': 'future'}
        replacement = json.dumps(changed).encode()
        status, invalid_value = 200, {'state': 'future', 'revision': '0'}
    path_open = Path.open
    fired = []

    def change_after_first_read(path, *args, **kwargs):
        stream = path_open(path, *args, **kwargs)
        if path != target or fired:
            return stream

        class SnapshotReader:
            def __enter__(self):
                stream.__enter__()
                return self

            def __exit__(self, *exc):
                return stream.__exit__(*exc)

            def read(self, size=-1):
                data = stream.read(size)
                if not fired:
                    fired.append(True)
                    target.write_bytes(replacement)
                return data

        return SnapshotReader()

    with monkeypatch.context() as scope:
        scope.setattr(Path, 'open', change_after_first_read)
        try:
            loaded = ContractBundle.load(root, integrity_sha256=external_digest)
        except ContractError:
            assert fired
            return
        assert fired
        # Either reject the changing tree or validate the authenticated snapshot.
        with pytest.raises(ContractError):
            loaded.validate_response('submit', status, invalid_value)
