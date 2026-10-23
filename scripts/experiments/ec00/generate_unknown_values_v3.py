#!/usr/bin/env python3
"""Generate the isolated v3 qualification from one TypeSpec source, offline.

OpenAPI is the SDK projection; independently emitted JSON Schema is the runtime
validation authority. No domain field/property list is maintained here.
"""
from __future__ import annotations
import argparse
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
EC00 = ROOT / 'tests/experiments/ec00'
CANDIDATES = EC00 / 'candidates'
DESTINATION = EC00 / 'generated/typespec-v3'
DIALECT = 'https://json-schema.org/draft/2020-12/schema'


def helpers():
    spec = importlib.util.spec_from_file_location('ec00_generator', Path(__file__).with_name('generate_candidates.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_bundle(bundle):
    """Finite, shared projection profile; domain property names stay opaque.

    Only these keywords are qualified for both runtimes. In particular default,
    format assertions, remote/dynamic refs and unqualified applicators cannot
    silently become no-ops. Content metadata is annotation only; original bytes
    are decoded/verified by the owner, never rewritten by the schema runtime.
    """
    if bundle.get('$schema') != DIALECT or not bundle.get('$defs'):
        raise ValueError('expected a bundled JSON Schema 2020-12 document')
    ids = {value.get('$id') for value in bundle['$defs'].values()}
    if None in ids or len(ids) != len(bundle['$defs']):
        raise ValueError('schema resources need unique local IDs')
    for resource in ids | {bundle.get('$id')}:
        if not isinstance(resource, str) or '/' in resource or ':' in resource or '#' in resource:
            raise ValueError('only local schema resource IDs are supported')
    annotations = {'title', 'description', '$comment', 'deprecated', 'readOnly',
                   'writeOnly', 'examples', 'contentEncoding', 'contentMediaType'}
    atoms = {'$id', '$schema', '$ref', 'type', 'enum', 'const', 'required',
             'minItems', 'maxItems', 'uniqueItems', 'minLength', 'maxLength',
             'pattern', 'minimum', 'maximum', 'exclusiveMinimum',
             'exclusiveMaximum', 'multipleOf', 'minProperties', 'maxProperties'}
    maps = {'properties', '$defs'}
    sequences = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    single = {'not', 'items', 'additionalProperties', 'unevaluatedProperties'}
    supported = annotations | atoms | maps | sequences | single
    def walk(schema):
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise ValueError('schema nodes must be objects or booleans')
        unknown = set(schema) - supported
        if unknown:
            raise ValueError('unsupported schema keywords: ' + ', '.join(sorted(unknown)))
        if '$schema' in schema and schema['$schema'] != DIALECT:
            raise ValueError('unsupported schema dialect')
        ref = schema.get('$ref')
        # Each emitted model is its own resource; this finite profile
        # supports declared resource IDs only, never fragment resolution.
        if ref is not None and (not isinstance(ref, str) or ref not in ids):
            raise ValueError(f'unsupported schema reference: {ref}')
        for key in maps & schema.keys():
            for child in schema[key].values():
                walk(child)
        for key in sequences & schema.keys():
            for child in schema[key]:
                walk(child)
        for key in single & schema.keys():
            walk(schema[key])
    walk(bundle)


def operation_manifest(openapi, bundle):
    validate_bundle(bundle)
    if openapi.get('openapi') != '3.0.0':
        raise ValueError('SDK projection must explicitly target OpenAPI 3.0.0')
    def schema_id(schema):
        ref = schema.get('$ref', '')
        prefix = '#/components/schemas/'
        if not ref.startswith(prefix):
            raise ValueError('operation boundaries require named generated schemas')
        name = ref[len(prefix):]
        return bundle['$defs'][name]['$id']
    operations = {}
    for path, methods in sorted(openapi['paths'].items()):
        for method, operation in sorted(methods.items()):
            if method not in ('get', 'post', 'patch', 'put', 'delete'):
                raise ValueError(f'unsupported OpenAPI path member {method}')
            name = operation['operationId']
            if name in operations or not name.isidentifier():
                raise ValueError('operation IDs must be unique identifiers')
            responses = operation['responses']
            if set(responses) != {'200'}:
                raise ValueError('this bounded projection requires one explicit 200 result')
            body = operation.get('requestBody')
            operations[name] = {
                'method': method.upper(), 'path': path,
                'request': schema_id(body['content']['application/json']['schema']) if body else None,
                'result': schema_id(responses['200']['content']['application/json']['schema']),
                'parameters': [{'name': param['name'], 'in': param['in'],
                                'schema': schema_id(param['schema'])}
                               for param in operation.get('parameters', [])],
            }
    return {'schemaBundle': bundle['$id'], 'operations': operations}


def attachment(bundle, manifest):
    # Schemas are copied mechanically, never translated into another rule language.
    prelude = '''// Generated by generate_unknown_values_v3.py; do not edit.
import Ajv2020 from "ajv/dist/2020.js";
import * as sdk from "./sdk.gen.js";
const ajv = new Ajv2020({ strict: true, strictTypes: false, allErrors: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false });
'''
    text = prelude + 'const bundle = ' + json.dumps(bundle, sort_keys=True) + ';\n'
    text += 'const operations = ' + json.dumps(manifest['operations'], sort_keys=True) + ' as const;\n'
    text += '''for (const schema of Object.values(bundle.$defs)) ajv.addSchema(schema);
export function schemaValidator(schemaId: string) {
  const validate = ajv.getSchema(schemaId);
  if (!validate) throw new Error(`Unknown generated schema: ${schemaId}`);
  return async (value: unknown) => {
    if (!validate(value)) throw new Error(`Generated schema refusal: ${ajv.errorsText(validate.errors)}`);
    return value;
  };
}
export function responseValidator(operation: keyof typeof operations) {
  return schemaValidator(operations[operation].result);
}
'''
    for name in manifest['operations']:
        text += f'''export const {name} = (options: Parameters<typeof sdk.{name}>[0]) =>
  sdk.{name}({{ ...options, responseValidator: responseValidator("{name}") }});
'''
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--check-root', type=Path, default=DESTINATION,
                        help='compare against a disposable mutated output tree for drift falsifiers')
    args = parser.parse_args()
    h = helpers()
    node = Path(os.environ.get('EC00_NODE', '/missing')).resolve()
    if not node.is_file() or h.digest(node) != h.NODE_SHA256:
        raise SystemExit('exact pinned EC00_NODE digest required')
    if sys.version_info[:3] != (3, 13, 16):
        raise SystemExit('pinned Python 3.13.16 required')
    for package, pin in {'openapi-python-client': '0.29.1', 'jsonschema': '4.26.0', 'httpx': '0.28.1'}.items():
        if version(package) != pin:
            raise SystemExit(f'Python pin mismatch: {package}')
    lock = json.loads((CANDIDATES / 'package-lock.json').read_text())
    for package, pin in {'@typespec/compiler': '1.17.0', '@typespec/http': '1.17.0',
                         '@typespec/openapi3': '1.17.0', '@typespec/json-schema': '1.17.0',
                         '@hey-api/openapi-ts': '0.99.0', 'ajv': '8.20.0', 'typescript': '5.9.3'}.items():
        installed = json.loads((CANDIDATES / 'node_modules' / package / 'package.json').read_text())['version']
        if installed != pin or lock['packages']['node_modules/' + package]['version'] != pin:
            raise SystemExit(f'npm installed/lock pin mismatch: {package}')
    generator = shutil.which('openapi-generator-cli')
    if not generator or h.output([generator, 'version'], cwd=CANDIDATES) != '7.26.0':
        raise SystemExit('locked OpenAPI Generator 7.26.0 required')
    bins = CANDIDATES / 'node_modules/.bin'
    env = dict(os.environ, PATH=f'{node.parent}:{bins}:{os.environ["PATH"]}')
    with tempfile.TemporaryDirectory(prefix='ec00-v3-generation-') as directory:
        work = Path(directory)
        out = work / 'typespec-v3'
        out.mkdir()
        h.run([str(bins / 'tsp'), 'compile', 'typespec/unknown-values-v3.tsp', '--config', 'tspconfig-v3.yaml',
               '--option', f'@typespec/openapi3.emitter-output-dir={out / "openapi"}',
               '--option', f'@typespec/json-schema.emitter-output-dir={out / "schema"}'], cwd=CANDIDATES, env=env)
        openapi_path = out / 'openapi/ec00-v3.openapi.json'
        schema_path = out / 'schema/ec00-v3.schema.json'
        bundle = json.loads(schema_path.read_text())
        manifest = operation_manifest(json.loads(openapi_path.read_text()), bundle)
        (out / 'operation-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        py = out / 'clients/python/ec00_oa_v3_python_client'
        py.parent.mkdir(parents=True)
        h.run([sys.executable, '-m', 'openapi_python_client', 'generate', '--meta', 'none', '--path', str(openapi_path),
               '--output-path', str(py), '--config', str(CANDIDATES / 'openapi-python-client-v3.yaml'), '--overwrite'], cwd=CANDIDATES, env=env)
        for path in py.rglob('*.py'):
            path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines()).rstrip() + '\n')
        ts = out / 'clients/typescript'
        h.run([str(bins / 'openapi-ts'), '-i', str(openapi_path), '-o', str(ts), '-c', '@hey-api/client-fetch', '-p', '@hey-api/sdk'], cwd=CANDIDATES, env=env)
        (ts / 'validated-sdk.ts').write_text(attachment(bundle, manifest))
        for language, package in [('go', 'ec00oa'), ('rust', 'ec00_oa_v3')]:
            raw = work / (language + '-raw')
            extra = f'packageName={package},hideGenerationTimestamp=true'
            if language == 'go':
                extra += ',moduleName=example.com/vuoro-ec00/openapi-go-v3,disallowAdditionalPropertiesIfNotPresent=false,enumUnknownDefaultCase=false'
            else:
                extra += ',library=reqwest'
            h.run([generator, 'generate', '-i', str(openapi_path), '-g', language, '-o', str(raw),
                   '--global-property', 'apiTests=false,modelTests=false,apiDocs=false,modelDocs=false',
                   '--additional-properties=' + extra], cwd=CANDIDATES, env=env)
            if language == 'go':
                mod = raw / 'go.mod'
                text = mod.read_text()
                placeholder = 'module github.com/GIT_USER_ID/GIT_REPO_ID'
                if placeholder not in text:
                    raise ValueError('Go module placeholder changed')
                mod.write_text(text.replace(placeholder, 'module example.com/vuoro-ec00/openapi-go-v3', 1))
            h.selected_tree(raw, out / 'clients' / language,
                            suffixes={'.go' if language == 'go' else '.rs'},
                            root_files={'go.mod' if language == 'go' else 'Cargo.toml'}, normalize_text=True)
        if args.write:
            shutil.rmtree(DESTINATION, ignore_errors=True)
            shutil.copytree(out, DESTINATION, ignore=shutil.ignore_patterns('.ruff_cache', '__pycache__'))
            print('v3 generated outputs updated; historical trees untouched')
        elif h.tree(out) != (h.tree(args.check_root) if args.check_root.exists() else {}):
            raise SystemExit('v3 generated output drift')
        else:
            print('v3 generation matches pinned inputs byte-for-byte')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
