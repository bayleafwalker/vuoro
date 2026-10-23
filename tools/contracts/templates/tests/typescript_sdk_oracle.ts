/** Executes actual emitted Hey API SDK plus its generated attachment, offline. */
import assert from 'node:assert/strict';
import Ajv2020 from '__AJV__';
import * as attachment from '__ATTACHMENT__';
import * as sdk from '__SDK__';
import {createClient} from '__CLIENT__';

const schemaBundle: any = __BUNDLE_LITERAL__;
const manifest: any = __MANIFEST_LITERAL__;
const ajv = new Ajv2020({strict: true, strictTypes: false, allErrors: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false});
for (const schema of Object.values(schemaBundle.$defs)) ajv.addSchema(schema as any);
const observations: any = (globalThis as any).__vuoroOracle = {
  native: 0, requestValidator: 0, serializer: 0, responseValidator: 0, errorParse: 0,
};
const dataEqual = (actual: unknown, expected: unknown) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected);
const encoder = new TextEncoder();
const textBytes = (value: string) => encoder.encode(value);
const ordinary = {revision: '9007199254740995', enabled: true, nested: {label: 'é'}};
const success = {state: 'ready', revision: '9007199254740995', metadata: {safe: {text: 'é'}}};
const counts = {wire: 0, effect: 0, serverResult: 0, streamPulls: 0, streamCancelled: 0};
let fault: string | undefined;
const outgoing: Uint8Array[] = [];
const ABSENT = Symbol('oracle_absent');
const knownCodes = new Set(['schema_profile','schema_value','manifest','integrity','operation','status','parameter','json','body_limit']);
(globalThis as any).fetch = async () => { throw new Error('oracle_live_fetch_forbidden'); };

function validate(schema: string | null, value: unknown) {
  if (schema === null) { assert.equal(value, ABSENT); return; }
  const checker = ajv.getSchema(schema);
  assert(checker && checker(value), 'server_oracle_schema_positive_is_invalid');
}
const fetcher: typeof fetch = async (input: RequestInfo | URL) => {
  assert(input instanceof Request);
  counts.wire++;
  const url = new URL(input.url);
  assert.equal(url.origin, 'https://contracts.invalid');
  assert.equal(url.search, '');
  const operation = url.pathname === '/probe/k/empty' ? 'empty' : url.pathname === '/probe/k/null' ? 'nullBody'
    : url.pathname === '/probe/k' ? (input.method === 'POST' ? 'submit' : 'inspect') : undefined;
  assert(operation, 'wrong_native_sdk_route');
  const op = manifest.operations[operation];
  const rawRequest = new Uint8Array(await input.arrayBuffer());
  outgoing.push(rawRequest);
  const value = rawRequest.length ? JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(rawRequest)) : ABSENT;
  validate(op.request, value);
  if (operation === 'submit') {
    assert.deepEqual(value, ordinary);
    assert.equal(typeof value.revision, 'string');
    counts.effect++;
  }
  let status = operation === 'empty' ? 204 : 200;
  let body: any = operation === 'empty' ? ABSENT : operation === 'nullBody' ? null : structuredClone(success);
  if (fault === 'valid400') { status = 400; body = {code: 'invalid'}; }
  if (fault === 'valid503') { status = 503; body = {code: 'unavailable'}; }
  validate(op.responses[String(status)], body);
  counts.serverResult++;
  // Faults happen after the valid server result and independent effect observation.
  let raw = body === ABSENT ? new Uint8Array() : textBytes(JSON.stringify(body));
  if (fault === 'future') raw = textBytes('{"state":"future","revision":"0"}');
  if (fault === 'duplicate') raw = textBytes('{"state":"ready","revision":"0","revision":"9007199254740995"}');
  if (fault === 'nan') raw = textBytes('{"state":"ready","revision":"0","metadata":{"x":NaN}}');
  if (fault === 'overflow') raw = textBytes('{"state":"ready","revision":"0","metadata":{"x":1e999}}');
  if (fault === 'invalid_utf8') raw = new Uint8Array([...textBytes('{"state":"ready","revision":"0","metadata":{"x":"'), 255, ...textBytes('"}}')]);
  if (fault === 'surrogate') raw = textBytes('{"state":"ready","revision":"0","metadata":{"x":"\\ud800"}}');
  if (fault === 'unknown_status') status = 201;
  if (fault === 'wrong400') { status = 400; raw = textBytes('{"code":"unavailable"}'); }
  if (fault === 'wrong503') { status = 503; raw = textBytes('{"code":"invalid"}'); }
  if (fault === 'empty_json' || fault === 'null_missing') raw = new Uint8Array();
  if (fault === 'null_json' || fault === 'empty_wrong_body') raw = textBytes('null');
  const cap = op.responseMaxBytes[String(status)];
  if (fault === 'bound_exact') raw = textBytes(new TextDecoder().decode(raw) + ' '.repeat(cap - raw.length));
  if (fault === 'oversize' || fault === 'stream') raw = textBytes(new TextDecoder().decode(raw) + ' '.repeat(4096));
  let stream: ReadableStream<Uint8Array> | undefined;
  if (fault === 'stream') {
    let sent = false;
    stream = new ReadableStream({
      pull(controller) {
        counts.streamPulls++;
        if (!sent) { sent = true; controller.enqueue(raw); }
        // Deliberately never close: a guard must cancel without waiting for EOF.
      },
      cancel() { counts.streamCancelled++; },
    }, {highWaterMark: 0});
  }
  if (status === 204 && raw.length) {
    // Fault injection only: WHATWG Response forbids constructing bodyful204.
    const response = new Response(raw, {status: 200, headers: {'Content-Type': 'application/json'}});
    Object.defineProperty(response, 'status', {value: 204});
    return response;
  }
  return new Response(status === 204 ? null : stream ?? raw, {status, headers: {'Content-Type': 'application/json'}});
};
const client: any = attachment.createValidatedClient({baseUrl: 'https://contracts.invalid', fetch: fetcher});
const request = () => ({path: {key: 'k'}, body: ordinary});
const refuse = async (run: () => Promise<unknown>) => {
  await assert.rejects(run, (error: any) => {
    assert(knownCodes.has(error.code), 'unsafe_or_untyped_sdk_error');
    assert(!String(error).includes('PRIVATE_'));
    assert(!JSON.stringify(error).includes('PRIVATE_'));
    return true;
  });
};
const within = <T>(value: Promise<T>) => {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('oracle_bounded_read_hung')), 1000); });
  return Promise.race([value, timeout]).finally(() => clearTimeout(timer));
};
const scenario = process.argv[2];
if (scenario === 'positive') {
  assert.equal(typeof attachment.strictDecode, 'function');
  const before = structuredClone(ordinary);
  const result = await client.submit(request());
  assert.equal(result.status, 200); dataEqual(result.body, success);
  assert(result.content instanceof Uint8Array); assert.deepEqual(result.content, textBytes(JSON.stringify(success)));
  assert.deepEqual(ordinary, before); assert.equal(result.body.revision, '9007199254740995');
  assert.deepEqual(counts, {wire: 1, effect: 1, serverResult: 1, streamPulls: 0, streamCancelled: 0});
  assert.equal(observations.native, 1); assert.equal(observations.requestValidator, 1);
  assert.equal(observations.serializer, 1); assert.equal(observations.responseValidator, 1);
  assert.notEqual(result.body, observations.nativeSuccessBody, 'return original strict decoder value rather than native JSON.parse result');
} else if (scenario === 'raw_request') {
  const invalids: any[] = [
    {...ordinary, unexpected: 'PRIVATE_RAW_MARKER'}, {...ordinary, revision: 9007199254740995},
    {...ordinary, revision: '9223372036854775808'}, {...ordinary, enabled: 1},
    {...ordinary, nested: {label: 'x', unexpected: true}}, {...ordinary, note: undefined},
    {...ordinary, note: '\ud800'}, {...ordinary, note: Number.NaN},
  ];
  for (const body of invalids) await refuse(() => client.submit({path: {key: 'k'}, body}));
  assert.equal(observations.serializer, 0); assert.equal(counts.wire, 0); assert.equal(counts.effect, 0);
  for (const note of [attachment.ABSENT, null, '']) {
    delete (ordinary as any).note;
    if (note !== attachment.ABSENT) (ordinary as any).note = note;
    const result = await client.submit(request()); assert.equal(result.status, 200);
    assert.equal(Object.hasOwn(JSON.parse(new TextDecoder().decode(outgoing.at(-1))), 'note'), note !== attachment.ABSENT);
  }
  assert.equal(counts.effect, 3);
} else if (scenario.startsWith('serialized_request:')) {
  observations.serializeMutation = scenario.split(':')[1];
  await refuse(() => client.submit(request()));
  assert.equal(observations.serializer, 1); assert.equal(counts.wire, 0); assert.equal(counts.effect, 0);
} else if (scenario === 'serialized_exact') {
  observations.serializeMutation = 'exact'; const result = await client.submit(request());
  assert.equal(result.status, 200); assert.equal(outgoing[0].length, 1024); assert.equal(counts.effect, 1);
} else if (scenario.startsWith('response:')) {
  fault = scenario.slice('response:'.length);
  await within(refuse(() => client.submit(request())));
  assert.equal(counts.wire, 1); assert.equal(counts.effect, 1); assert.equal(counts.serverResult, 1);
  assert.equal(observations.responseValidator, 0); assert.equal(observations.errorParse, 0);
  if (fault === 'stream') { assert.equal(counts.streamCancelled, 1); assert(counts.streamPulls <= 2); }
} else if (scenario === 'status') {
  for (const [kind, status, body] of [['valid400', 400, {code: 'invalid'}], ['valid503', 503, {code: 'unavailable'}]] as const) {
    fault = kind;
    const result = await client.submit(request()); assert.equal(result.status, status); dataEqual(result.body, body);
  }
  assert.equal(observations.responseValidator, 0); assert.equal(counts.effect, 2);
} else if (scenario === 'absence') {
  const empty = await client.empty({path: {key: 'k'}});
  assert.equal(empty.status, 204); assert.equal(empty.body, attachment.ABSENT); assert.equal(empty.content.length, 0);
  const nullable = await client.nullBody({path: {key: 'k'}});
  assert.equal(nullable.status, 200); assert.equal(nullable.body, null); assert.deepEqual(nullable.content, textBytes('null'));
  fault = 'empty_wrong_body'; await refuse(() => client.empty({path: {key: 'k'}}));
  fault = 'null_missing'; await refuse(() => client.nullBody({path: {key: 'k'}}));
} else if (scenario === 'bound_exact') {
  fault = 'bound_exact'; const result = await client.submit(request());
  assert.equal(result.content.length, manifest.operations.submit.responseMaxBytes['200']); dataEqual(result.body, success);
} else if (scenario === 'concurrent_status') {
  let release!: () => void; let entered!: () => void;
  const paused = new Promise<void>((resolve) => { release = resolve; });
  const atHook = new Promise<void>((resolve) => { entered = resolve; });
  observations.beforeSuccess = async () => { entered(); await paused; };
  const a = client.submit(request()); await within(atHook);
  fault = 'valid503'; const b = await client.submit(request()); release(); const first = await within(a);
  assert.equal(first.status, 200); dataEqual(first.body, success);
  assert.equal(b.status, 503); dataEqual(b.body, {code: 'unavailable'});
  assert.equal(observations.responseValidator, 1); assert.equal(counts.effect, 2);
} else if (scenario === 'attachment_removal') {
  fault = 'duplicate';
  const bare: any = await sdk.submit({client: createClient({baseUrl: 'https://contracts.invalid', fetch: fetcher}), ...request(), throwOnError: true});
  assert.equal(bare.response.status, 200); assert.equal(bare.data.revision, '9007199254740995');
  assert.equal(counts.effect, 1); await refuse(() => client.submit(request())); assert.equal(counts.effect, 2);
} else if (scenario === 'unsupported_options') {
  for (const override of ['fetch','client','bodySerializer','requestValidator','responseValidator','responseTransformer','parseAs','throwOnError']) {
    await refuse(() => client.submit({...request(), [override]: override === 'parseAs' ? 'text' : () => undefined}));
  }
  assert.equal(counts.wire, 0); assert.equal(observations.serializer, 0);
} else { throw new Error('unknown_oracle_scenario'); }
console.log(JSON.stringify({scenario, ...counts, ...Object.fromEntries(Object.entries(observations).filter(([, value]) => typeof value === 'number'))}));
