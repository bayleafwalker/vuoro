"""v3 qualification: actual four-language SDKs, frozen Q1 policy, one owner.

Faults are injected after server-side result validation so decoder refusals must
occur on real HTTP200 responses. No native decoder filtering is introduced.
"""
from contextlib import closing
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import httpx
from artifact_semantics import _TEST_PRIVATE_KEY, canonical_bytes, make_signed_artifact, transport_fields
from candidate_owner_http import initialize_fixture, persisted_artifact
from owner_oracle import OwnerOracle, material_digest
from schema_owner_http_v3 import SchemaOwnerHandler, SchemaOwnerServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'generated/typespec-v3/clients/python'))
from ec00_oa_v3_python_client.client import Client
from ec00_oa_v3_python_client.api.default import read_request, read_artifact

REQUEST_ID = '9007199254741199'
LARGE_REVISION = '9007199254740995'
ORIGINAL = b'\x00{"futureSchema":{"opaque":"preserve exact spaces"} }\xff'
VARIANTS = ('baseline', 'additive-top', 'additive-nested', 'unknown-state', 'note-absent', 'note-empty')
LANGUAGES = ('python', 'typescript', 'go', 'rust')


def snapshot(path):
    with closing(sqlite3.connect(path)) as database:
        return {table: database.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                for table in ('request', 'event', 'idem', 'dependent', 'signed_artifact')}


def snapshot_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        default=lambda item: {'bytes_hex': item.hex()}).encode()).hexdigest()


def seed(path, *, large_revision=False):
    initialize_fixture(path)
    body = {'objective': '', 'note': None}
    signed = make_signed_artifact(workspace_id='workspace-a', request_id=REQUEST_ID,
        material_revision=1, material_digest=material_digest(body), original_bytes=ORIGINAL)
    owner = OwnerOracle.connect_as(path, 'requester-a')
    try:
        owner.publish(REQUEST_ID, body, ['work-a'], 'seed', signed_artifact=signed)
    finally:
        owner.close()
    if large_revision:
        with closing(sqlite3.connect(path)) as database:
            database.execute('UPDATE request SET revision=? WHERE request_id=?', (int(LARGE_REVISION), REQUEST_ID))
            database.commit()
    return signed


class ResponseVariantHandler(SchemaOwnerHandler):
    def _json(self, status, body):
        body = copy.deepcopy(body)
        if status == 200 and 'state' in body and 'body' in body:
            variant = self.server.variant
            if variant == 'additive-top':
                body['futureMetadata'] = {'diagnostic': 'safe additive fixture'}
            elif variant == 'additive-nested':
                body['body']['futureMetadata'] = {'diagnostic': 'safe additive fixture'}
            elif variant == 'unknown-state':
                body['state'] = 'future-success'
            elif variant == 'note-absent':
                del body['body']['note']
            elif variant == 'note-empty':
                body['body']['note'] = ''
        self.server.observed_wire.append({'path': self.path, 'status': status, 'body': copy.deepcopy(body)})
        super()._json(status, body)


class ResponseVariantServer(SchemaOwnerServer):
    def __init__(self, path, variant):
        super().__init__(path, handler=ResponseVariantHandler)
        self.httpd.variant = variant
        self.httpd.observed_wire = []


def python_read(base_url):
    def observe(function):
        try:
            response = function()
            if response.status_code != 200 or response.parsed is None:
                return {'decoded': False, 'error': f'HTTP {response.status_code}'}
            return {'decoded': True, 'value': response.parsed.to_dict()}
        except (ValueError, KeyError, TypeError) as error:
            return {'decoded': False, 'error': type(error).__name__}
    with Client(base_url=base_url, timeout=httpx.Timeout(5)) as client:
        return {'language': 'python',
                'request': observe(lambda: read_request.sync_detailed(REQUEST_ID, client=client)),
                'artifact': observe(lambda: read_artifact.sync_detailed(REQUEST_ID, '1', client=client))}


def native_read(language, base_url, *, omit_validator=False):
    candidates = HERE / 'candidates'
    env = {**os.environ, 'EC00_BASE_URL': base_url, 'EC00_UNKNOWN_VALUES_V3': '1',
           'CARGO_NET_OFFLINE': 'true', 'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOTOOLCHAIN': 'local'}
    env.pop('EC00_V3_VALIDATOR_FALSIFIER', None)
    if omit_validator:
        env['EC00_V3_VALIDATOR_FALSIFIER'] = 'omit'
    with tempfile.TemporaryDirectory(prefix='ec00-v3-native-') as temp:
        if language == 'typescript':
            node = env.get('EC00_NODE')
            if not node:
                raise RuntimeError('EC00_NODE missing: pinned runtime could not be checked')
            env['PATH'] = str(Path(node).parent) + os.pathsep + env['PATH']
            env['NODE_PATH'] = str(candidates / 'node_modules')
            target = Path(temp) / 'runner.cjs'
            build = subprocess.run([str(candidates / 'node_modules/.bin/esbuild'), str(candidates / 'run_unknown_values_v3.ts'),
                '--bundle', '--platform=node', '--format=cjs', '--log-level=warning', '--outfile=' + str(target)],
                cwd=candidates, env=env, capture_output=True, text=True)
            if build.returncode:
                raise RuntimeError('TypeScript build could not be checked: ' + build.stderr[-3000:])
            command = [node, str(target)]
        elif language == 'go':
            if not env.get('EC00_GO'):
                raise RuntimeError('EC00_GO missing: pinned runtime could not be checked')
            command = [env['EC00_GO'], 'run', '-mod=readonly', './candidates/go-client-v3']
        elif language == 'rust':
            if not env.get('EC00_CARGO'):
                raise RuntimeError('EC00_CARGO missing: pinned runtime could not be checked')
            command = [env['EC00_CARGO'], 'run', '--quiet', '--offline', '--locked', '--manifest-path',
                       str(candidates / 'rust-client/Cargo.toml'), '--bin', 'unknown_v3']
        else:
            raise ValueError('unknown native language')
        process = subprocess.run(command, cwd=HERE, env=env, capture_output=True, text=True, timeout=300)
        if process.returncode:
            raise RuntimeError(f'{language} runtime failed, not a decoder result: {process.stderr[-3000:]}')
    value = json.loads(process.stdout)
    if value.get('language') != language:
        raise ValueError('runner language mismatch')
    return value


def read_findings(row, expected_artifact):
    """External acceptance guard retains Q1 semantics and strengthens presence."""
    findings = []
    prefix = f"{row['variant']}/{row['language']}"
    wire = row.get('wire', [])
    paths = ['/v3/requests/' + REQUEST_ID, '/v3/requests/' + REQUEST_ID + '/artifact/1']
    if [entry.get('path') for entry in wire] != paths or any(entry.get('status') != 200 for entry in wire):
        findings.append(prefix + ': both exact generated reads must reach HTTP200')
    elif wire[0]['body'].get('state') != ('future-success' if row['variant'] == 'unknown-state' else 'open'):
        findings.append(prefix + ': response fault/control not exercised')
    request = row['request']
    if row['variant'] == 'unknown-state':
        if request.get('decoded'):
            findings.append(prefix + ': unknown lifecycle decoded as typed success')
    elif not request.get('decoded'):
        findings.append(prefix + ': baseline/additive/presence control refused')
    else:
        value = request['value']
        known = {'requestId': REQUEST_ID, 'revision': LARGE_REVISION, 'materialRevision': '1',
                 'state': 'open', 'reported': False, 'verified': False, 'createdSeq': '1'}
        if any(value.get(key) != expected for key, expected in known.items()):
            findings.append(prefix + ': known response meaning changed')
        body = value.get('body', {})
        if body.get('objective') != '':
            findings.append(prefix + ': empty objective changed')
        if row['variant'] == 'note-absent':
            if 'note' in body:
                findings.append(prefix + ': absent note became a value')
        elif row['variant'] == 'note-empty':
            if body.get('note', None) != '':
                findings.append(prefix + ': empty note lost')
        elif body.get('note', 'absent') is not None:
            findings.append(prefix + ': explicit null note lost')
    artifact = row['artifact']
    if not artifact.get('decoded'):
        findings.append(prefix + ': original artifact not decoded')
    else:
        for field, expected in expected_artifact.items():
            if artifact['value'].get(field) != expected:
                findings.append(prefix + ': original artifact field changed: ' + field)
    if not row.get('owner_unchanged'):
        findings.append(prefix + ': read changed owner state')
    return findings


def command_findings(row):
    if row['case'] == 'known-command-control':
        return [] if row['status'] == 200 and not row['owner_unchanged'] and all(
            row['counts_after'][table] == row['counts_before'][table] + 1 for table in ('event', 'idem')) else ['inert positive command']
    return [] if row['status'] in (409, 422) and row['owner_unchanged'] else [
        f"{row['case']}: expected explicit refusal without owner effect"]


def command_case(directory, case):
    path = directory / (case + '.sqlite')
    seed(path)
    before = snapshot(path)
    method, route = 'PATCH', '/v3/requests/' + REQUEST_ID
    body = {'requestId': REQUEST_ID, 'expectedRevision': '1', 'body': {'objective': 'changed', 'note': ''}}
    revisions = {'null-revision': None, 'zero-revision': '0', 'numeric-revision': 1,
                 'leading-zero-revision': '01', 'empty-revision': '', 'negative-revision': '-1',
                 'out-of-range-revision': '9223372036854775808', 'newline-revision': '1\n'}
    if case in revisions:
        body['expectedRevision'] = revisions[case]
    elif case == 'missing-revision':
        del body['expectedRevision']
    elif case == 'unknown-command-field':
        body['futureAuthority'] = {'grant': True}
    elif case == 'unknown-nested-command-field':
        body['body']['futureAuthority'] = {'grant': True}
    elif case in ('unknown-nested-signed-field', 'unknown-authority-intent'):
        method, route = 'POST', '/v3/requests'
        new_id = '9007199254741299'
        semantic_body = {'objective': 'signed command'}
        signed = make_signed_artifact(workspace_id='workspace-a', request_id=new_id,
            material_revision=1, material_digest=material_digest(semantic_body), original_bytes=ORIGINAL)
        envelope = json.loads(signed['envelope_bytes'])
        if case == 'unknown-nested-signed-field':
            envelope['futureAuthority'] = {'grant': True}
        else:
            envelope['intent'] = 'future-authority'
        signed['envelope_bytes'] = canonical_bytes(envelope)
        signed['signature'] = _TEST_PRIVATE_KEY.sign(signed['envelope_bytes'])
        body = {'requestId': new_id, 'body': semantic_body, 'dependants': ['work-a'], 'artifact': transport_fields(signed)}
    elif case != 'known-command-control':
        raise ValueError('unfrozen command case')
    with SchemaOwnerServer(path) as server:
        response = httpx.request(method, server.base_url + route, json=body,
            headers={'Idempotency-Key': 'v3-' + case}, timeout=5, trust_env=False)
    after = snapshot(path)
    return {'case': case, 'transport': 'raw HTTP through generated schema to unchanged owner',
            'status': response.status_code, 'wire_request': body, 'wire_response': response.json(),
            'owner_unchanged': before == after, 'before_sha256': snapshot_digest(before),
            'after_sha256': snapshot_digest(after), 'counts_before': {k: len(v) for k, v in before.items()},
            'counts_after': {k: len(v) for k, v in after.items()}}


def collect_matrix(directory):
    path = directory / 'reads.sqlite'
    signed = seed(path, large_revision=True)
    before = snapshot(path)
    expected = {'materialRevision': '1', 'materialDigest': material_digest({'objective': '', 'note': None}),
        'artifactDigest': signed['artifact_digest'], 'keyId': signed['key_id'],
        **{field: base64.b64encode(signed[source]).decode() for field, source in (
            ('originalBytes', 'original_bytes'), ('envelopeBytes', 'envelope_bytes'), ('signature', 'signature'))}}
    rows, infrastructure = [], []
    for variant in VARIANTS:
        with ResponseVariantServer(path, variant) as server:
            for language in LANGUAGES:
                server.httpd.observed_wire.clear()
                try:
                    row = python_read(server.base_url) if language == 'python' else native_read(language, server.base_url)
                except Exception as error:
                    infrastructure.append({'variant': variant, 'language': language, 'status': 'could-not-check', 'error': str(error)[-3500:]})
                    continue
                row.update(variant=variant, wire=copy.deepcopy(server.httpd.observed_wire), owner_unchanged=before == snapshot(path))
                rows.append(row)
    cases = ('known-command-control', 'unknown-command-field', 'unknown-nested-command-field',
             'unknown-nested-signed-field', 'unknown-authority-intent', 'missing-revision', 'null-revision',
             'zero-revision', 'numeric-revision', 'leading-zero-revision', 'empty-revision', 'negative-revision',
             'out-of-range-revision', 'newline-revision')
    commands = [command_case(directory, case) for case in cases]
    findings = [finding for row in rows for finding in read_findings(row, expected)]
    findings.extend(finding for row in commands for finding in command_findings(row))
    if persisted_artifact(path, REQUEST_ID, 1) != (signed['original_bytes'], signed['envelope_bytes'], signed['signature'], signed['artifact_digest']):
        findings.append('persisted original signed artifact changed')
    return {'schema': 'ec00-unknown-values-qualification/v3', 'scope': 'Disposable loopback v3; no production/ingress/performance claim',
            'expected_artifact': expected, 'rows': rows, 'commands': commands, 'could_not_check': infrastructure,
            'findings': findings, 'qualified': not findings and not infrastructure and len(rows) == len(VARIANTS) * len(LANGUAGES),
            'limitations': ['Four-language response qualification; raw command corpus does not prove four-language request serialization',
                            'Native generated SDKs are qualified for these values, not claimed as general JSON Schema engines'],
            'toolchain_environment': {key: os.environ.get(key) for key in ('EC00_NODE', 'EC00_GO', 'EC00_CARGO')}}


class UnknownValueV3Checks(unittest.TestCase):
    def test_actual_four_language_v3_unknown_value_policy(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-unknown-values-') as temp:
            report = collect_matrix(Path(temp))
        destination = os.environ.get('EC00_UNKNOWN_V3_REPORT')
        if destination:
            Path(destination).write_text(json.dumps(report, indent=2) + '\n')
        self.assertFalse(report['could_not_check'], json.dumps(report['could_not_check']))
        self.assertEqual(24, len(report['rows']))
        self.assertFalse(report['findings'], '\n'.join(report['findings']))
        self.assertTrue(report['qualified'])

    def test_real_http_omitted_sdk_validator_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-sdk-falsifier-') as temp:
            path = Path(temp) / 'owner.db'
            signed = seed(path, large_revision=True)
            before = snapshot(path)
            expected = {'materialRevision': '1', 'materialDigest': material_digest({'objective': '', 'note': None}),
                'artifactDigest': signed['artifact_digest'], 'keyId': signed['key_id'],
                **{field: base64.b64encode(signed[source]).decode() for field, source in (
                    ('originalBytes', 'original_bytes'), ('envelopeBytes', 'envelope_bytes'), ('signature', 'signature'))}}
            with ResponseVariantServer(path, 'unknown-state') as server:
                row = native_read('typescript', server.base_url, omit_validator=True)
                row.update(variant='unknown-state', wire=copy.deepcopy(server.httpd.observed_wire), owner_unchanged=before == snapshot(path))
            self.assertTrue(row['request']['decoded'], 'falsifier did not actually omit SDK validation')
            self.assertTrue(any('unknown lifecycle' in finding for finding in read_findings(row, expected)))
            self.assertTrue(row['owner_unchanged'])

    def test_read_guard_rejects_bad_controls(self):
        expected = {field: 'original' for field in ('originalBytes', 'envelopeBytes', 'signature', 'artifactDigest')}
        row = {'variant': 'unknown-state', 'language': 'fixture', 'request': {'decoded': False},
            'artifact': {'decoded': True, 'value': copy.deepcopy(expected)}, 'owner_unchanged': True,
            'wire': [{'path': '/v3/requests/' + REQUEST_ID, 'status': 200, 'body': {'state': 'future-success'}},
                     {'path': '/v3/requests/' + REQUEST_ID + '/artifact/1', 'status': 200, 'body': {}}]}
        self.assertEqual([], read_findings(row, expected))
        row['request'] = {'decoded': True, 'value': {'state': 'future-success'}}
        self.assertTrue(read_findings(row, expected))
        row['request'] = {'decoded': False}
        row['artifact']['value']['originalBytes'] = 'rewritten'
        self.assertTrue(read_findings(row, expected))
        row['artifact']['value'] = copy.deepcopy(expected)
        row['variant'] = 'baseline'
        self.assertTrue(read_findings(row, expected))
        row['variant'] = 'unknown-state'
        row['wire'][0]['status'] = 500
        self.assertTrue(read_findings(row, expected))

    def test_command_guard_rejects_silent_effect_and_inert_control(self):
        base = {'case': 'unknown-command-field', 'status': 422, 'owner_unchanged': True}
        self.assertEqual([], command_findings(base))
        self.assertTrue(command_findings({**base, 'status': 200, 'owner_unchanged': False}))
        self.assertTrue(command_findings({**base, 'owner_unchanged': False}))
        self.assertTrue(command_findings({**base, 'case': 'known-command-control'}))


if __name__ == '__main__':
    unittest.main()
