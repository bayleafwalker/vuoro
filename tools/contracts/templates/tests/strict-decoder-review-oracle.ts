/** Proposed independent decoder controls. Bind __DECODER__ to a disposable source copy. */
import assert from 'node:assert/strict';
import {ContractError, strictDecode, validateJsonValue} from '__DECODER__';
const HARD = 4 * 1024 * 1024;
const encode = (value: string) => new TextEncoder().encode(value);
const marker = 'PRIVATE_UNTRUSTED_ERROR_ATTRIBUTE_20261023';
const safeFailure = (run: () => unknown) => {
  let captured: any;
  try { run(); } catch (error) { captured = error; }
  assert(captured, 'expected_refusal');
  assert.equal(captured.code, 'json');
  assert.deepEqual(captured.path, []);
  assert(!String(captured).includes(marker));
  assert(!JSON.stringify(captured).includes(marker));
  assert(!('payload' in captured));
  assert(!('cause' in captured));
};
const limit = (run: () => unknown) => assert.throws(run, (error: any) => error.code === 'body_limit');
const scenario = process.argv[2];
if (scenario === 'shadow_small') {
  const raw = encode('123'); Object.defineProperty(raw, 'byteLength', {value: 1});
  limit(() => strictDecode(raw, 1));
} else if (scenario === 'shadow_hard') {
  const raw = encode(' '.repeat(HARD) + '0'); Object.defineProperty(raw, 'byteLength', {value: 1});
  limit(() => strictDecode(raw, HARD));
} else if (scenario === 'privacy_recycled_internal') {
  let saved: any;
  try { strictDecode(encode('['), 1); } catch (error) { saved = error; }
  assert(saved, 'initial_internal_refusal');
  try { saved.path.push(marker); } catch {}
  try { saved.payload = marker; } catch {}
  try { saved.message = marker; } catch {}
  safeFailure(() => validateJsonValue(new Proxy({}, {getPrototypeOf() { throw saved; }})));
} else if (scenario === 'privacy_forged_value') {
  const foreign: any = new ContractError('json', [marker]); foreign.payload = marker;
  const input = new Proxy({}, {getPrototypeOf() { throw foreign; }});
  safeFailure(() => validateJsonValue(input));
} else if (scenario === 'privacy_forged_bytes') {
  const foreign: any = new ContractError('json', [marker]); foreign.payload = marker;
  const raw = new Uint8Array([0x30]);
  Object.defineProperty(raw, 'byteLength', {get() { throw foreign; }});
  safeFailure(() => strictDecode(raw, 1));
} else if (scenario === 'privacy_thrown_proxy') {
  const foreign = new Proxy({}, {getPrototypeOf() { throw new Error(marker); }});
  const input = new Proxy({}, {ownKeys() { throw foreign; }});
  safeFailure(() => validateJsonValue(input));
} else if (scenario === 'raw_scalar_limit') {
  const exact = 'x'.repeat(HARD - 2);
  assert.equal(validateJsonValue(exact), exact);
  limit(() => validateJsonValue(exact + 'x'));
} else if (scenario === 'raw_utf8_limit') {
  const exact = 'é'.repeat((HARD - 2) / 2);
  assert.equal(validateJsonValue(exact), exact);
  limit(() => validateJsonValue(exact + 'x'));
} else if (scenario === 'raw_escape_limit') {
  const exact = '\u0000'.repeat(Math.floor((HARD - 2) / 6)) + 'x'.repeat((HARD - 2) % 6);
  assert.equal(validateJsonValue(exact), exact);
  limit(() => validateJsonValue(exact + 'x'));
} else if (scenario === 'raw_aggregate_limit') {
  const overhead = encode(JSON.stringify({left:'', right:''})).length;
  const value = {left: 'x'.repeat(Math.floor((HARD - overhead) / 2)), right: 'x'.repeat(Math.ceil((HARD - overhead) / 2))};
  assert.equal(validateJsonValue(value), value);
  value.right += 'x';
  limit(() => validateJsonValue(value));
} else if (scenario === 'raw_key_limit') {
  const key = 'é'.repeat((HARD - 6) / 2);
  const exact = {[key]: 0};
  assert.equal(validateJsonValue(exact), exact);
  limit(() => validateJsonValue({[key + 'x']: 0}));
} else if (scenario === 'valid_json_corpus') {
  let seed = 50231;
  const random = () => ((seed = (1664525 * seed + 1013904223) >>> 0) / 2**32);
  const strings = ['', 'a', 'é😀', '\u0000\b\f\n\r\t', '"\\/', 'a\ufeffb', 'e\u0301', '\u007f'];
  const value = (depth = 0): any => {
    const kind = Math.floor(random() * (depth < 4 ? 7 : 5));
    if (kind === 0) return null;
    if (kind === 1) return random() < .5;
    if (kind === 2) return (random() - .5) * 1e20;
    if (kind === 3) return [0, -0, 1e-300, 1e300][Math.floor(random() * 4)];
    if (kind === 4) return strings[Math.floor(random() * strings.length)];
    if (kind === 5) return Array.from({length: Math.floor(random() * 5)}, () => value(depth + 1));
    const object: any = {};
    for (let i = 0, n = Math.floor(random() * 5); i < n; i++) {
      Object.defineProperty(object, strings[Math.floor(random() * strings.length)] + i,
        {value: value(depth + 1), enumerable: true, writable: true, configurable: true});
    }
    return object;
  };
  for (let i = 0; i < 2000; i++) {
    const original = value(); const raw = JSON.stringify(original);
    assert.deepEqual(strictDecode(encode(raw), encode(raw).length), JSON.parse(raw));
    assert.equal(validateJsonValue(original), original);
  }
  assert(Object.is(strictDecode(encode('-0'), 2), -0));
  assert.deepEqual(strictDecode(encode('{"é":1,"e\\u0301":2}'), 64), {'é':1, 'é':2});
  const storage = new Uint8Array([255, 123, 125, 255]);
  assert.deepEqual(strictDecode(storage.subarray(1, 3), 2), {});
  const shared = {label: 'same'}; const alias = {a: shared, b: shared};
  assert.equal(validateJsonValue(alias), alias);
} else { throw new Error('unknown_regression_scenario'); }
console.log(JSON.stringify({scenario, result:'pass'}));
