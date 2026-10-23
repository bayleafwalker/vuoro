"""Generic local-resource JSON Schema validator for generated operation boundaries.

No fetching, defaults, coercion or mutation. The supplied manifest and schemas
are generated together; domain properties are never re-authored here.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

GENERATED = Path(__file__).resolve().parent / 'generated/typespec-v3'
DIALECT = 'https://json-schema.org/draft/2020-12/schema'


def _no_remote(uri):
    raise NoSuchResource(ref=uri)


class SchemaValidation:
    def __init__(self, root=GENERATED):
        self.root = Path(root)
        self.manifest = json.loads((self.root / 'operation-manifest.json').read_text())
        bundle_id = self.manifest['schemaBundle']
        if not isinstance(bundle_id, str) or '/' in bundle_id or ':' in bundle_id or '#' in bundle_id:
            raise ValueError('schema bundle must be a local filename')
        self.bundle = json.loads((self.root / 'schema' / bundle_id).read_text())
        # The same finite supported-feature gate runs at generation and load.
        # This imports generic generator code, never a second domain schema.
        generator = Path(__file__).resolve().parents[3] / 'scripts/experiments/ec00/generate_unknown_values_v3.py'
        spec = importlib.util.spec_from_file_location('ec00_v3_schema_profile', generator)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate_bundle(self.bundle)
        schemas = {item['$id']: item for item in self.bundle['$defs'].values()}
        registry = Registry(retrieve=_no_remote).with_resources(
            (identifier, Resource.from_contents(schema)) for identifier, schema in schemas.items())
        self.validators = {}
        for identifier, schema in schemas.items():
            Draft202012Validator.check_schema(schema)
            self.validators[identifier] = Draft202012Validator(schema, registry=registry)
        for operation in self.manifest['operations'].values():
            for identifier in [operation['request'], operation['result'],
                               *(param['schema'] for param in operation['parameters'])]:
                if identifier is not None and identifier not in schemas:
                    raise ValueError('operation references an unknown schema')

    def validate_schema(self, identifier, value):
        self.validators[identifier].validate(value)
        return value

    def validate(self, operation, direction, value):
        if direction not in ('request', 'result'):
            raise ValueError('unsupported boundary direction')
        identifier = self.manifest['operations'][operation][direction]
        if identifier is None:
            if value is not None:
                raise ValueError('operation has no request body')
            return value
        return self.validate_schema(identifier, value)
