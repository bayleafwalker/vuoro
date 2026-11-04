import asyncio
import json

import httpx
import pytest

from test_read_tools import OwnerSource, _caller, _tool, REPO
from vuoro_mcp_edge.read_tools import EFFECT_PREVIEW
from vuoro_mcp_edge.toolsets import ToolFailure

ID = 'intent_' + '0' * 26


def projection():
    return {'schema': 'sprintctl-declared-effect-preview/v1',
        'basis': {'intent_id': ID, 'revision': 1, 'canonical_intent_digest': 'a' * 64,
                  'state': 'proposed', 'item_id': 1, 'repository': '/private/repo',
                  'base_commit': 'b' * 40, 'release_digest': None},
        'unified_diff_sha256': 'c' * 64,
        'declared_changes': {'status': 'parsed', 'path_count': 2, 'paths': [], 'redacted': True, 'truncated': False},
        'resources': {'status': 'not-declared'}, 'protected_policy': {'status': 'unknown'},
        'external_consequences': {'status': 'unknown'},
        'duplicate_relation': {'status': 'unknown', 'candidate_intent_ids': [], 'complete': False,
            'scope': 'authorized proposed intents, same repository/item/release/content'},
        'receipt_source': {'operation': 'work.effect.get-v1', 'acceptance': 'missing', 'application': 'missing'},
        'authorization': 'none; owner acceptance and execution checks remain required'}


class Source(OwnerSource):
    def __init__(self, response=None):
        super().__init__({EFFECT_PREVIEW: response or {'repo_id': REPO, 'preview': projection()}})

    async def _ensure_catalog(self):
        self._read_operations = frozenset(self.operations)

    async def _invoke(self, op, args, forwarded, *, require_read=False):
        assert require_read is True
        return await super()._invoke(op, args, forwarded)


def test_preview_redacts_repository_and_forwards_only_explicit_redacted_request():
    source = Source()
    tool = _tool(source, 'preview_effect')
    assert asyncio.run(tool.describe())['name'] == 'preview_effect'
    result = asyncio.run(tool.run(tool.parse({'intent_id': ID}), _caller()))
    assert result['declared_changes']['path_count'] == 2
    assert '/private/repo' not in json.dumps(result)
    assert source.calls == [(EFFECT_PREVIEW, {'intent_id': ID, 'disclose_paths': False}, REPO)]


def test_owner_legal_repository_whitespace_is_withheld():
    preview = projection()
    preview['basis']['repository'] = 'repo\nname'
    tool = _tool(Source({'repo_id': REPO, 'preview': preview}), 'preview_effect')
    result = asyncio.run(tool.run(tool.parse({'intent_id': ID}), _caller()))
    assert 'repo\nname' not in json.dumps(result)


def test_advertisement_requires_validated_read_catalog():
    source = Source()
    async def no_read():
        source._read_operations = frozenset()
    source._ensure_catalog = no_read
    assert asyncio.run(_tool(source, 'preview_effect').describe()) is None


@pytest.mark.parametrize('kind', ['missing-revision', 'duplicate', 'non-read'])
def test_real_catalog_validation_hides_invalid_or_nonread_capability(kind):
    from vuoro_mcp_edge.work_source import ShellWorkSource, REQUIRED_OPERATIONS
    rows = [{'name': name, 'execution_semantics': 'read'} for name in REQUIRED_OPERATIONS]
    rows.append({'name': EFFECT_PREVIEW, 'execution_semantics': 'write' if kind == 'non-read' else 'read'})
    if kind == 'duplicate':
        rows.append({'name': EFFECT_PREVIEW, 'execution_semantics': 'read'})
    payload = {'operations': rows}
    if kind != 'missing-revision':
        payload['revision'] = 'catalog-1'
    source = ShellWorkSource(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    async def check():
        try:
            return await _tool(source, 'preview_effect').describe()
        finally:
            await source.aclose()
    assert asyncio.run(check()) is None


def test_owner_duplicate_candidates_are_not_enumerated():
    preview = projection()
    preview['duplicate_relation'] = {
        'status': 'observed', 'candidate_intent_ids': ['intent_' + '1' * 26],
        'complete': True,
        'scope': 'authorized proposed intents, same repository/item/release/content',
    }
    tool = _tool(Source({'repo_id': REPO, 'preview': preview}), 'preview_effect')
    result = asyncio.run(tool.run(tool.parse({'intent_id': ID}), _caller()))
    assert 'duplicate_relation' not in result
    assert 'intent_' + '1' * 26 not in json.dumps(result)


@pytest.mark.parametrize('args', [{}, {'intent_id': ID, 'disclose_paths': True},
    {'intent_id': ID, 'repo_id': 'foreign'}, {'intent_id': '../private'}, {'intent_id': ID, 'wake': True}])
def test_invalid_arguments_never_forward(args):
    source = Source()
    with pytest.raises(ToolFailure):
        _tool(source, 'preview_effect').parse(args)
    assert not source.calls


def test_capability_must_be_exact():
    source = Source()
    source.operations.clear()
    assert asyncio.run(_tool(source, 'preview_effect').describe()) is None


@pytest.mark.parametrize('mutation', ['foreign', 'paths', 'extra', 'scope', 'id', 'count', 'credential'])
def test_malformed_or_private_owner_response_fails_closed(mutation):
    response = {'repo_id': REPO, 'preview': projection()}
    p = response['preview']
    if mutation == 'foreign': response['repo_id'] = 'foreign'
    if mutation == 'paths': p['declared_changes']['paths'] = [{'path': '/private/secret'}]
    if mutation == 'extra': p['unified_diff'] = 'private content'
    if mutation == 'scope': p['duplicate_relation']['scope'] = 'token=secret'
    if mutation == 'id': p['basis']['intent_id'] = 'intent_' + '1' * 26
    if mutation == 'count': p['declared_changes']['path_count'] = True
    if mutation == 'credential': p['receipt_source']['acceptance'] = 'secret-token'
    tool = _tool(Source(response), 'preview_effect')
    with pytest.raises(ToolFailure) as error:
        asyncio.run(tool.run(tool.parse({'intent_id': ID}), _caller()))
    assert error.value.code == 'owner-response-invalid'
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('authorities', [['work:read'], ['work.effect.get'], []])
def test_edge_denies_missing_authority_before_owner_post(tmp_path, authorities):
    from edge_support import assertion, call, identity_headers, key_pair, resolver
    from vuoro_mcp_edge.server import create_edge_app
    from vuoro_mcp_edge.read_tools import build_toolset
    from vuoro_mcp_edge.runs import UnavailableRunRegistry
    from vuoro_mcp_edge.toolsets import ToolsetContext
    keys = key_pair(tmp_path)
    source = Source()
    app = create_edge_app(identity_resolver=resolver(keys[0]), work_source=source,
        toolsets=(build_toolset(ToolsetContext(env={}, work_source=source, runs=UnavailableRunRegistry())),))
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://edge') as client:
            return await client.post('/mcp', headers=identity_headers(assertion(keys[1], authorities=authorities)),
                                     json=call('preview_effect', {'intent_id': ID}))
    response = asyncio.run(send())
    if authorities:
        assert response.json()['result']['structuredContent']['error']['code'] == 'authority-required'
    else:
        assert response.status_code == 401
    assert source.calls == []
