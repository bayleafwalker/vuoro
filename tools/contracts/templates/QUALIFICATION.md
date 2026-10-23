# SDK attachment admission

These root-owned templates are instantiated by the production generator, with
real compiler/native SDK source and attachment qualification. They contain explicit
generator placeholders and cannot serve traffic as written. The TS decoder is a
mandatory qualified injection, with no JSON.parse fallback. Local source gates
cover bounded UTF-8, duplicate-key/nonfinite parsing and actual native serializers;
canonical CI and real-owner F2 remain separate acceptance gates.

Hey API 0.99.0 success responseValidator runs on parsed JSON success only and has
no status argument. A separate raw fetch guard must validate *every* declared
status/body (including 4xx/5xx, 204 and empty bodies) before returning the Response.
The actual success hook must also be observed. Per-call status context cannot be
shared across concurrent requests. Verify empty vs absent vs JSON null; an absent
body has null schema/null byte bound. A JSON body requires positive byte bounds.
The byte count applies to bytes, not Unicode string length. Bound response reads
before buffering. Request guards must check both raw values before model conversion
and actual serialized bytes; removing/dropping unknown data before validation fails.

The Python sync hook runs before generated model conversion and checks serialized
request bytes. The generated entrypoint additionally validates the caller's raw
request before to_dict(). Exact method/path matching and request/response parameter
validation must be emitted without ambiguous matching. Unsupported async, body or
status routes reject. No implicit retries, server authority or deployment is added.

The F1b root-owned immutable qualification gates are: generate both SDKs from actual owner
TypeSpec; run their real APIs against bounded in-process HTTPX MockTransport and a
TS fetch fake; observe exactly one wire request plus independent real owner effect;
fault the bytes after server validation; unknown state/status/code, duplicate keys,
nonfinite JSON, invalid UTF-8, overflow and byte limit must visibly refuse; remove
both attachments independently and observe unsafe behavior caught by the oracle;
concurrent statuses do not cross-bind; two clean generations and mutations to each
schema/status map/SDK/attachment all drift. Mock transport proves SDK attachment
only. F2 must exercise real Sovite owner state, journals, fences and replay behavior.
No server/TestClient/socket test may run here without root escalation.

## F1b immutable attachment ABI and executable oracle

The F1a profile/runtime and pure generator helpers are frozen. F1b fills the CLI
and generates attachments; the existing partial templates are not qualified SDK
implementations. Root must admit their complete generic implementation and strict
decoder before claiming the F1b oracle executable end to end. No generator may
fall back to a plain JSON.parse decoder or skip absent tooling.

The Python output package is `vuoro_generated_contract_client` (the root-pinned
openapi-python-client config). Its `validated_client` module exports
`create_validated_client(*, client: <actual generated Client>,
integrity_sha256: str)`. The result has emitted operation methods: `submit(key=,
body=)`, `inspect(key=)`, `empty(key=)`, and `null_body(key=)` for this supporting
source. Each returns a frozen envelope exposing `status: int`, `body` (the original
strictly decoded JSON value, or `vuoro_contracts.ABSENT`), and `content: bytes`.
The factory loads the packaged generated bundle with the externally pinned digest.
It calls the actual generated endpoint `sync_detailed`; it does not reimplement a
parallel SDK or return generated model conversions in place of the decoded body.
No async entry point is qualified. Raw caller values validate before model
construction/to_dict; request hooks independently validate the actual serialized
bytes before transport; response hooks bound and validate the actual received
status/body before `_parse_response` or any generated response model conversion.
Known declared 4xx/5xx responses return their validated status/body envelope;
unknown status/code/body is a safe ContractError, never a raw SDK error/body leak.

The TS attachment `validated-sdk.ts` exports `ABSENT`, `strictDecode(raw:
Uint8Array, maxBytes: number)`, and
`createValidatedClient({baseUrl: string, fetch: typeof fetch})`. Each emitted method
accepts its declared `{path,query,headers,body}` fields only (absent groups are
omitted) and returns `{status: number, body, content: Uint8Array}`. This supporting
source uses `submit({path:{key},body})`, `inspect({path:{key}})`,
`empty({path:{key}})`, and `nullBody({path:{key}})`. Caller overrides of client,
fetch, serializers, validators, transformers, parse modes or throw behavior refuse.
Each call owns fresh status/raw-byte/body context; none is shared across requests.
The raw fetch guard handles every declared status, including non-2xx and204,
before the generated client parses anything. Also install and exercise the actual
Hey API requestValidator and JSON-success responseValidator hooks. The factory
returns the original validated decoder value, with no model rewrite or synthesized
empty object. Its embedded contract is authenticated by the distribution/build
inventory outside the attachment; embedding its own inventory digest would be
self-referential and is forbidden.

`strictDecode` requires a positive integral byte cap, strict UTF-8, exact JSON
grammar, duplicate decoded keys at every depth, finite numbers (including overflow
refusal), and valid Unicode scalar strings/keys after escape decoding. Lone UTF-16
surrogates refuse; valid paired escapes and UTF-8 emoji preserve their value.
Ref-shaped JSON data and `__proto__` remain ordinary safe own properties, never
prototype mutation. Error codes/path/message expose no untrusted values. Raw JS
caller values must also be finite JSON before serialization; undefined, NaN,
functions, symbols, bigint, cycles and lone surrogates cannot silently disappear
or normalize through JSON.stringify. Explicit optional absence, null and empty
string remain distinct. Physical serialized/request/response UTF-8 bytes are
bounded independently of F1a canonical-value bounds, counting whitespace too.
A bounded read must cancel on overflow without awaiting EOF or hanging on an
unconsumed Response.clone tee branch.

The independent oracles live in `templates/tests/` and execute real freshly
emitted SDKs. Python spies on native sync_detailed/_get_kwargs/_parse_response;
TS adds only counters/barriers at exact qualified generated-client lines in a
disposable copy. Neither adds a production instrumentation API. Post-server result
faults are introduced after the valid server result and independent effect
observation; their refusal must preserve that observation. Native SDK calls with
attachments omitted accept a duplicate-key response, while the guarded call
refuses it: this is the attachment-removal control for both languages. The simple
in-process effect observer is transport evidence, never a new real owner or F2.

Root supplies explicit `VUORO_CONTRACT_NODE` and `VUORO_CONTRACT_PYTHON` executable
paths with frozen hashes and versions. TypeScript test bundling uses the locked
esbuild0.28.2 API directly with explicit nodePaths into `tools/contracts/node_modules`;
it does not invoke npx or an ambient PATH binary. tsx4.23.13 remains an available
locked tool, not an alternative fallback. Root must also run the pinned TS5.9.3
type gate against real generated output. No new registry or decoder dependency is
admitted by these tests. Locks, current source-package byte identity and exact
runtime proof are root preparation, separate from license/support/restore and
upgrade/rollback acceptance.

The separately admitted decoder source is `templates/strict-json.ts`, exporting
ContractError, strictDecode and validateJsonValue. The latter validates a raw JS
JSON value without copying/coercion, rejects non-JSON/surrogate values before
serialization, and never evaluates accessors. Generator output copies this source
unchanged as `clients/typescript/strict-json.ts`; the attachment imports it and
reexports strictDecode. Its independent unit oracle binds the source through
explicit `VUORO_CONTRACT_DECODER`, before main/SDK orchestration exists. The full
oracle checks the copied module, attachment return path, and decoder-file drift.

Python nullable response qualification remains explicitly blocked for the stock
0.29.1 projection: allOf+nullable is flattened before endpoint templates, and the
native null_body parser calls Result.from_dict(None). The supported custom
`python-templates/endpoint_module.py.jinja` contains a GENERATION-only
__NULLABLE_STATUS_TABLE__ placeholder, method->path->[exact status], which root
must fill mechanically from the independently emitted named validation schemas.
Only those bindings gain a null return branch and Response[T|None] annotations.
It does not change emitted documents, add domain property lists, patch SDK instances
or swallow model errors. Root must generate and exercise this custom native SDK
(null/nonnull positives, nonnullable-null negative) before the F1b worker freeze.
