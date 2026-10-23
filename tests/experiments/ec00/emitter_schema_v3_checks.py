"""Independent rejecting oracles for actual v3 emitters and SDK return validation."""
from __future__ import annotations
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from jsonschema.exceptions import ValidationError
from schema_validation_v3 import GENERATED, SchemaValidation

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GENERATOR = ROOT / 'scripts/experiments/ec00/generate_unknown_values_v3.py'


def sample_read():
    return {'requestId': '9007199254741199', 'revision': '9007199254740995',
            'materialRevision': '1', 'state': 'open', 'body': {'objective': '', 'note': None},
            'reported': False, 'verified': False, 'createdSeq': '9007199254740997'}


def schema_corpus():
    command = {'requestId': '19', 'expectedRevision': '1', 'body': {'objective': '', 'note': None}}
    response = sample_read()
    rows = [dict(name='valid-command', schema='ReviseRequest.json', value=command, valid=True),
            dict(name='valid-response', schema='RequestRead.json', value=response, valid=True)]
    cases = [
        ('unknown-top-command', {**command, 'futureAuthority': True}),
        ('unknown-nested-command', {**command, 'body': {**command['body'], 'futureAuthority': True}}),
        ('null-body', {**command, 'body': None}),
        ('missing-objective', {**command, 'body': {'note': None}}),
        ('numeric-revision', {**command, 'expectedRevision': 1}),
        ('null-revision', {**command, 'expectedRevision': None}),
        ('leading-zero', {**command, 'expectedRevision': '01'}),
        ('negative-revision', {**command, 'expectedRevision': '-1'}),
        ('newline-revision', {**command, 'expectedRevision': '1\n'}),
        ('newline-request-id', {**command, 'requestId': '19\n'}),
        ('out-of-range', {**command, 'expectedRevision': '9223372036854775808'}),
        ('missing-revision', {key: value for key, value in command.items() if key != 'expectedRevision'}),
    ]
    rows.extend(dict(name=name, schema='ReviseRequest.json', value=value, valid=False) for name, value in cases)
    for revision in ('0', '1', '9007199254740995', '9223372036854775806', '9223372036854775807'):
        rows.append(dict(name='bounded-' + revision, schema='ReviseRequest.json',
                         value={**command, 'expectedRevision': revision}, valid=True))
    for note in ({}, {'note': ''}, {'note': None}):
        rows.append(dict(name='presence-' + repr(note), schema='ReviseRequest.json',
                         value={**command, 'body': {'objective': '', **note}}, valid=True))
    rows.extend([
        dict(name='additive-top-result', schema='RequestRead.json', value={**response, 'futureMetadata': {'safe': True}}, valid=True),
        dict(name='additive-nested-result', schema='RequestRead.json', value={**response, 'body': {**response['body'], 'futureMetadata': {'safe': True}}}, valid=True),
        dict(name='unknown-state', schema='RequestRead.json', value={**response, 'state': 'future-success'}, valid=False),
        dict(name='missing-state', schema='RequestRead.json', value={k: v for k, v in response.items() if k != 'state'}, valid=False),
        dict(name='null-state', schema='RequestRead.json', value={**response, 'state': None}, valid=False),
    ])
    return rows


def run_typescript(source):
    node = os.environ.get('EC00_NODE')
    if not node:
        raise RuntimeError('pinned EC00_NODE required; runtime could not be checked')
    env = dict(os.environ, NODE_PATH=str(HERE / 'candidates/node_modules'))
    esbuild = HERE / 'candidates/node_modules/.bin/esbuild'
    env['PATH'] = str(Path(node).parent) + os.pathsep + env['PATH']
    with tempfile.TemporaryDirectory(prefix='ec00-v3-ts-oracle-') as temp:
        target = Path(temp) / 'oracle.cjs'
        subprocess.run([str(esbuild), '--bundle', '--loader=ts', '--platform=node', '--format=cjs', '--log-level=warning', '--outfile=' + str(target)],
                       input=source, text=True, check=True, cwd=HERE / 'candidates', env=env, capture_output=True)
        completed = subprocess.run([node, str(target)], text=True, check=True, capture_output=True, env=env)
        return json.loads(completed.stdout)


def assert_sdk_policy(rows):
    for row in rows:
        if row['name'] == 'unknown-state':
            if row['decoded']:
                raise AssertionError('unknown lifecycle promoted to typed SDK success')
        elif not row['decoded'] or row['value'] != row['wire']:
            raise AssertionError('known/additive SDK result lost meaning')
    if {row['name'] for row in rows} != {'baseline', 'additive-top', 'additive-nested', 'unknown-state'}:
        raise AssertionError('incomplete SDK controls')


class EmitterSchemaV3Checks(unittest.TestCase):
    def test_actual_emitters_and_python_corpus(self):
        validation = SchemaValidation()
        openapi = json.loads((GENERATED / 'openapi/ec00-v3.openapi.json').read_text())
        self.assertEqual(openapi['openapi'], '3.0.0')
        self.assertEqual(validation.bundle['$schema'], 'https://json-schema.org/draft/2020-12/schema')
        self.assertEqual(set(validation.manifest['operations']), {'publish', 'revise', 'readRequest', 'readArtifact'})
        for name in ('PublishRequest', 'ReviseRequest', 'CommandBody', 'SignedArtifactInput'):
            self.assertEqual(openapi['components']['schemas'][name]['additionalProperties'], {'not': {}})
            self.assertEqual(validation.bundle['$defs'][name]['unevaluatedProperties'], {'not': {}})
        for name in ('MutationResult', 'RequestRead', 'ArtifactRead', 'RequestBody'):
            self.assertEqual(openapi['components']['schemas'][name]['additionalProperties'], {})
            self.assertEqual(validation.bundle['$defs'][name]['unevaluatedProperties'], {})
        for row in schema_corpus():
            with self.subTest(row=row['name']):
                if row['valid']:
                    before = copy.deepcopy(row['value'])
                    validation.validate_schema(row['schema'], row['value'])
                    self.assertEqual(row['value'], before)
                else:
                    with self.assertRaises(ValidationError):
                        validation.validate_schema(row['schema'], row['value'])

    def test_same_corpus_in_generated_ajv_validator(self):
        attachment = GENERATED / 'clients/typescript/validated-sdk.ts'
        source = f'import {{ schemaValidator }} from {json.dumps(str(attachment))};\n'
        source += '(async () => { const rows = ' + json.dumps(schema_corpus()) + '; const results = [];\n'
        source += '''for (const row of rows) {
          const before = JSON.stringify(row.value); let valid = true;
          try { await schemaValidator(row.schema)(row.value); } catch { valid = false; }
          results.push({name: row.name, valid, unchanged: before === JSON.stringify(row.value)});
        }
        console.log(JSON.stringify(results)); })().catch(error => { console.error(error); process.exit(1); });'''
        observed = run_typescript(source)
        self.assertEqual([(r['name'], r['valid'], r['unchanged']) for r in observed],
                         [(r['name'], r['valid'], True) for r in schema_corpus()])

    def test_validation_is_on_real_generated_sdk_return_path(self):
        ts = GENERATED / 'clients/typescript'
        source = '\n'.join([
            f'import * as validated from {json.dumps(str(ts / "validated-sdk.ts"))};',
            f'import * as raw from {json.dumps(str(ts / "sdk.gen.ts"))};',
            f'import {{ createClient }} from {json.dumps(str(ts / "client/index.ts"))};',
            '(async () => { const baseline = ' + json.dumps(sample_read()) + ';',
            '''const outputs = {};
            for (const mode of ["validated", "omitted", "wrong-schema"]) {
              const rows = [];
              for (const name of ["baseline", "additive-top", "additive-nested", "unknown-state"]) {
                const wire = structuredClone(baseline);
                if (name === "additive-top") wire.futureMetadata = {safe: true};
                if (name === "additive-nested") wire.body.futureMetadata = {safe: true};
                if (name === "unknown-state") wire.state = "future-success";
                let calls = 0;
                const client = createClient({baseUrl: "http://fixture.invalid", fetch: async () => {
                  calls++; return new Response(JSON.stringify(wire), {status: 200, headers: {"Content-Type": "application/json"}});
                }});
                let decoded = false; let value;
                try {
                  const options = {client, path: {requestId: wire.requestId}, throwOnError: true};
                  const response = mode === "validated" ? await validated.readRequest(options)
                    : await raw.readRequest({...options, ...(mode === "wrong-schema"
                      ? {responseValidator: validated.schemaValidator("MutationResult.json")} : {})});
                  decoded = response.response.status === 200 && response.data !== undefined;
                  value = response.data;
                } catch {}
                if (calls !== 1) throw new Error("SDK did not reach the supplied HTTP fetch exactly once");
                rows.push({name, wire, decoded, value});
              }
              outputs[mode] = rows;
            }
            console.log(JSON.stringify(outputs)); })().catch(error => { console.error(error); process.exit(1); });''',
        ])
        observed = run_typescript(source)
        assert_sdk_policy(observed['validated'])
        # Both concrete bad attachments really return unknown lifecycle success;
        # the acceptance oracle must reject them, not merely inspect source text.
        for mode in ('omitted', 'wrong-schema'):
            with self.assertRaisesRegex(AssertionError, 'unknown lifecycle'):
                assert_sdk_policy(observed[mode])

    def test_unsupported_features_and_references_fail_closed(self):
        spec = importlib.util.spec_from_file_location('ec00_v3_generation', GENERATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for key, value in [('$ref', 'https://fixture.invalid/schema.json'), ('$ref', 'missing.json'),
                           ('$dynamicRef', '#future'), ('default', {'objective': 'injected'}),
                           ('if', {'required': ['futureAuthority']}), ('minLenght', 99),
                           ('format', 'unqualified-assertion')]:
            with tempfile.TemporaryDirectory(prefix='ec00-v3-bad-schema-') as temp:
                root = Path(temp)
                shutil.copytree(GENERATED / 'schema', root / 'schema')
                shutil.copyfile(GENERATED / 'operation-manifest.json', root / 'operation-manifest.json')
                bundle = copy.deepcopy(SchemaValidation().bundle)
                bundle['$defs']['ReviseRequest']['properties']['body'] = {key: value}
                (root / 'schema/ec00-v3.schema.json').write_text(json.dumps(bundle))
                with self.assertRaises(ValueError):
                    SchemaValidation(root)
                with self.assertRaises(ValueError):
                    module.validate_bundle(bundle)

    def test_generation_refuses_fragment_references(self):
        spec = importlib.util.spec_from_file_location('ec00_v3_generation', GENERATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        openapi = json.loads((GENERATED / 'openapi/ec00-v3.openapi.json').read_text())
        baseline = SchemaValidation().bundle
        # Positive control exercises the actual generation manifest boundary.
        self.assertEqual(set(module.operation_manifest(openapi, baseline)['operations']),
                         {'publish', 'revise', 'readRequest', 'readArtifact'})
        for reference in ('#/$defs/NoSuchSchema', '#/$defs/CommandBody'):
            with self.subTest(reference=reference):
                bundle = copy.deepcopy(baseline)
                bundle['$defs']['ReviseRequest']['properties']['body'] = {'$ref': reference}
                with self.assertRaisesRegex(ValueError, 'unsupported schema reference'):
                    module.operation_manifest(openapi, bundle)

    def test_runtime_load_refuses_fragment_references(self):
        # The loader must reject before any request can encounter resolution.
        # Even an outer-bundle definition name is not a local model resource ref.
        SchemaValidation()  # Positive control: current named-resource refs load.
        for reference in ('#/$defs/NoSuchSchema', '#/$defs/CommandBody'):
            with self.subTest(reference=reference), tempfile.TemporaryDirectory(prefix='ec00-v3-fragment-') as temp:
                root = Path(temp)
                shutil.copytree(GENERATED / 'schema', root / 'schema')
                shutil.copyfile(GENERATED / 'operation-manifest.json', root / 'operation-manifest.json')
                bundle = copy.deepcopy(SchemaValidation().bundle)
                bundle['$defs']['ReviseRequest']['properties']['body'] = {'$ref': reference}
                (root / 'schema/ec00-v3.schema.json').write_text(json.dumps(bundle))
                with self.assertRaisesRegex(ValueError, 'unsupported schema reference'):
                    SchemaValidation(root)

    def test_known_annotations_are_noninjecting_metadata(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-annotations-') as temp:
            root = Path(temp)
            shutil.copytree(GENERATED / 'schema', root / 'schema')
            shutil.copyfile(GENERATED / 'operation-manifest.json', root / 'operation-manifest.json')
            bundle = copy.deepcopy(SchemaValidation().bundle)
            bundle['$defs']['ReviseRequest'].update(title='Qualification annotation',
                description='Metadata creates no property or owner authority.', examples=[{'futureAuthority': True}])
            (root / 'schema/ec00-v3.schema.json').write_text(json.dumps(bundle))
            validation = SchemaValidation(root)
            value = {'requestId': '19', 'expectedRevision': '1', 'body': {'objective': ''}}
            original = copy.deepcopy(value)
            validation.validate('revise', 'request', value)
            self.assertEqual(value, original)

    def test_generated_ajv_refuses_an_unknown_schema_keyword(self):
        with tempfile.TemporaryDirectory(prefix='ec00-v3-ajv-keyword-') as temp:
            target = Path(temp) / 'typescript'
            shutil.copytree(GENERATED / 'clients/typescript', target)
            attachment = target / 'validated-sdk.ts'
            content = attachment.read_text()
            self.assertIn('"maxLength"', content)
            attachment.write_text(content.replace('"maxLength"', '"minLenght"', 1))
            source = f'import {{ schemaValidator }} from {json.dumps(str(attachment))};\n'
            source += 'schemaValidator("CommandBody.json");'
            with self.assertRaises(subprocess.CalledProcessError) as failure:
                run_typescript(source)
            self.assertIn('unknown keyword', failure.exception.stderr)

    def test_regeneration_and_four_independent_drift_falsifiers(self):
        # A full regeneration each time proves comparison is coupled to source,
        # not a self-reported artifact hash. Never mutate committed output bytes.
        targets = [('request-schema', 'schema/ec00-v3.schema.json'),
                   ('result-schema', 'schema/ec00-v3.schema.json'),
                   ('attachment', 'clients/typescript/validated-sdk.ts'),
                   ('sdk', 'clients/typescript/sdk.gen.ts')]
        with tempfile.TemporaryDirectory(prefix='ec00-v3-drift-') as temp:
            comparison = Path(temp) / 'generated'
            shutil.copytree(GENERATED, comparison)
            command = [sys.executable, str(GENERATOR), '--check-root', str(comparison)]
            clean = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            for kind, relative in targets:
                target = comparison / relative
                original = target.read_bytes()
                if kind.endswith('schema'):
                    document = json.loads(original)
                    model = 'ReviseRequest' if kind == 'request-schema' else 'RequestRead'
                    document['$defs'][model]['required'] = []
                    target.write_text(json.dumps(document))
                else:
                    target.write_bytes(original + b'\n// concrete drift falsifier\n')
                result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0, kind)
                self.assertIn('v3 generated output drift', result.stdout + result.stderr)
                target.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
