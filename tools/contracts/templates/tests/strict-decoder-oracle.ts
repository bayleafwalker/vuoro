/** Independent decoder corpus; __ATTACHMENT__ is replaced by the Python oracle. */
import assert from 'node:assert/strict';
import { strictDecode, validateJsonValue } from '__DECODER__';

const wireVectors: any = JSON.parse(__WIRE_VECTORS_JSON__);
const dataEqual = (actual: unknown, expected: unknown) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected);
const bytes = (value: string) => new TextEncoder().encode(value);
const decode = (value: string): any => strictDecode(bytes(value), bytes(value).byteLength);
let negativeControls = 0;
const refused = (raw: Uint8Array, max: number, code = 'json') => {
  negativeControls++;
  assert.throws(() => strictDecode(raw, max), (error: any) => {
    assert.equal(error.code, code);
    assert(!String(error).includes('PRIVATE_DECODER_MARKER'));
    assert(!JSON.stringify(error).includes('PRIVATE_DECODER_MARKER'));
    return true;
  });
};

for (const vector of wireVectors.valid) {
  const raw = bytes(vector.raw);
  dataEqual(strictDecode(raw, raw.byteLength), vector.value);
}
for (const vector of wireVectors.invalid) {
  const raw = vector.base64 === undefined ? bytes(vector.raw) :
    new Uint8Array(Buffer.from(vector.base64, 'base64'));
  refused(raw, Math.max(1, raw.byteLength));
}
const protoVector: any = strictDecode(bytes('{"__proto__":{"polluted":true}}'), 64);
assert(Object.hasOwn(protoVector, '__proto__'));
assert.equal((Object.prototype as any).polluted, undefined);
for (const value of [Number('9007199254740992'), 1e30]) {
  assert.throws(() => validateJsonValue(value), (error: any) => error.code === 'json');
}

dataEqual(decode('{"x":"é😀","n":null,"b":false,"a":[1,-2.5e3]}'),
  {x: 'é😀', n: null, b: false, a: [1, -2500]});
assert.equal(decode('"\\ud83d\\ude00"'), '😀');
assert.equal(decode('{"revision":"9007199254740995"}').revision, '9007199254740995');
const quoted = {a: 'literal { "x": 1, "x": 2 }'};
dataEqual(decode(JSON.stringify(quoted)), quoted);
const exact = bytes('{"safe":"é😀"}');
dataEqual(strictDecode(exact, exact.length), {safe: 'é😀'});
refused(exact, exact.length - 1, 'body_limit');
assert.equal(new TextDecoder('utf-8', {fatal: true}).decode(bytes(decode('"é😀"'))), 'é😀');
for (const text of [
  '{"x":1,"x":2}', '{"x":{"y":1,"y":2}}', '{"x":1,"\\u0078":2}',
  '{"é":1,"\\u00e9":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}',
  '{"x":1e999}', '{"x":-1e999}', '', '{} trailing', '[1,]', '{"x":01}',
  '{"x":"\\ud800"}', '{"x":"\\udfff"}', '{"\\ud800":1}',
  '{"x":"PRIVATE_DECODER_MARKER","x":1}',
]) refused(bytes(text), 4096);
refused(new Uint8Array([0xff]), 16);
refused(new Uint8Array([0xc0, 0xaf]), 16);
refused(new Uint8Array([0xed, 0xa0, 0x80]), 16);
refused(new Uint8Array([0xef, 0xbb, 0xbf, 0x7b, 0x7d]), 16);
for (const limit of [0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) refused(bytes('0'), limit, 'body_limit');
const proto: any = decode('{"__proto__":{"polluted":true}}');
assert(Object.hasOwn(proto, '__proto__'));
assert.equal((Object.prototype as any).polluted, undefined);
assert.equal(proto.__proto__.polluted, true);

const original = {safe: {label: 'é😀'}, revision: '9007199254740995', optional: null};
assert.equal(validateJsonValue(original), original);
const sameArray = [original, true, 3]; assert.equal(validateJsonValue(sameArray), sameArray);
const rejectValue = (value: unknown) => {
  negativeControls++;
  assert.throws(() => validateJsonValue(value), (error: any) => {
    assert.equal(error.code, 'json');
    assert(!String(error).includes('PRIVATE_DECODER_MARKER'));
    assert(!JSON.stringify(error).includes('PRIVATE_DECODER_MARKER'));
    return true;
  });
};
const cycle: any = {}; cycle.self = cycle;
const hole = new Array(1);
for (const value of [undefined, () => 1, Symbol('x'), 1n, Number.NaN, Infinity,
  new Date(0), new Map(), cycle, hole, {note: undefined}, {note: '\ud800'},
  {'PRIVATE_DECODER_MARKER': {unsafe: Number.NaN}}, {[Symbol('hidden')]: true}]) rejectValue(value);
let getterReads = 0;
const getter = Object.defineProperty({}, 'value', {enumerable: true, get() { getterReads++; return 1; }});
rejectValue(getter); assert.equal(getterReads, 0);
console.log(JSON.stringify({scenario: 'strict_decoder', negativeControls}));
