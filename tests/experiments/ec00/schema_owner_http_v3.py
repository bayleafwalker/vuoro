"""Disposable v3 schema boundary; all domain meaning stays in OwnerOracle."""
from __future__ import annotations
import json
import re
from urllib.parse import unquote, urlsplit
from jsonschema.exceptions import ValidationError
from candidate_owner_http import CandidateOwnerServer, _Handler
from owner_oracle import Conflict, ResponseLost
from schema_validation_v3 import SchemaValidation


class SchemaOwnerHandler(_Handler):
    def _route(self):
        for name, operation in self.server.validation.manifest['operations'].items():
            if operation['method'] != self.command:
                continue
            pattern = re.sub(r'\{[^}]+\}', '([^/]+)', operation['path'])
            match = re.fullmatch(pattern, urlsplit(self.path).path)
            if not match:
                continue
            parameters = {}
            path_parameters = [p for p in operation['parameters'] if p['in'] == 'path']
            for parameter, value in zip(path_parameters, match.groups(), strict=True):
                parameters[parameter['name']] = self.server.validation.validate_schema(parameter['schema'], unquote(value))
            for parameter in operation['parameters']:
                if parameter['in'] == 'header':
                    self.server.validation.validate_schema(parameter['schema'], self.headers.get(parameter['name']))
                elif parameter['in'] != 'path':
                    raise ValueError('unsupported routing parameter')
            return name, parameters
        return None, {}

    def _result_projection(self, operation, result):
        return result

    def _handle(self):
        try:
            operation, parameters = self._route()
            if operation is None:
                self._json(404, {'error': 'unsupported operation/version'})
                return
            request_schema = self.server.validation.manifest['operations'][operation]['request']
            body = json.loads(self._read()) if request_schema else None
            self.server.validation.validate(operation, 'request', body)
            key = self.headers.get('Idempotency-Key', '')
            if operation == 'publish':
                result = self._call_owner('publish', body['requestId'], body['body'], body['dependants'], key,
                    signed_artifact=self._artifact_from_json(body['artifact']) if 'artifact' in body else None)
                result = {'requestId': result['request_id'], 'revision': str(result['revision'])}
            elif operation == 'revise':
                if body['requestId'] != parameters['requestId']:
                    raise ValueError('path/body request identity mismatch')
                result = self._call_owner('revise', parameters['requestId'], int(body['expectedRevision']), body['body'], key)
                result = {'requestId': result['request_id'], 'revision': str(result['revision'])}
            elif operation == 'readRequest':
                result = self._request_read(self._call_owner('request', parameters['requestId']))
            elif operation == 'readArtifact':
                result = self._artifact_read(self._call_owner('signed_artifact', parameters['requestId'], int(parameters['materialRevision'])))
            else:
                raise ValueError('generated operation has no owner projection')
        except Conflict as error:
            self._json(409, {'error': str(error)})
            return
        except ResponseLost:
            self.close_connection = True
            return
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            self._json(422, {'error': str(error)})
            return
        try:
            result = self._result_projection(operation, result)
            self.server.validation.validate(operation, 'result', result)
        except (ValidationError, ValueError, TypeError, KeyError) as error:
            self._json(500, {'error': 'invalid owner result projection: ' + str(error)})
            return
        self._json(200, result)

    do_GET = _handle
    do_POST = _handle
    do_PATCH = _handle


class SchemaOwnerServer(CandidateOwnerServer):
    def __init__(self, path, *, principal_id='requester-a', fault_plan=None, handler=SchemaOwnerHandler):
        super().__init__(path, principal_id=principal_id, fault_plan=fault_plan)
        self.httpd.validation = SchemaValidation()
        self.httpd.RequestHandlerClass = handler
