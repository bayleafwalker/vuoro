# EC-00 disposable qualification workspace

This directory is an experiment input, not a Vuoro production contract, schema,
API, selected architecture, or owner implementation. It exists to make the
architecture comparison reproducible after EC-01 provides observed consumers
and payload classes. Do not publish these Python objects as an API.

## Initial source and toolchain observations

The initial EC-00 baseline was recorded before a generated-client toolchain was
assembled; its tool-availability statements below are historical and are
superseded by the later executable slice in this document.

Prepared from Vuoro commit `6c5a8ea96b100cf45697f7317e7f7fb6227146ee` in a
detached `/tmp` worktree. Vuoro's `AGENTS.md` assigns state-machine and owner
semantics to domain repositories and keeps `vuoro-client` transport-only.

At preparation time, the interactive PATH exposes Python 3.14.7, uv 0.13.0,
pytest 9.1.1 and SQLite 3.53.3 through Python's standard library. `protoc`,
`buf`, `tsp`, Node, Go, Rust, and Cargo are not on PATH. Nix store inspection
finds executable `protoc` 35.1/36.1/36.2, Node 22.23.2/24.21.0 and Go
1.26.8/1.27.2 binaries, but these are cached artifacts rather than an
experiment pin. No Buf, TypeSpec, rustc or Cargo executable was found. Vuoro's
current Python lock has Pydantic 2.13.4 but no protobuf, Connect, gRPC,
TypeSpec or code-generator dependency. A future candidate must pin compiler,
plugins, generated runtimes and all language versions in a disposable
reproducible toolchain before measurements. Do not install vendors or infer
support from an unrelated cached binary.

The source checkout has no Go module, Rust manifest, TypeScript package, `.proto`
file, Buf config or TypeSpec config. EC-01 must establish whether those
languages/runtimes are actual consumers before they are mandatory prototype
targets.

The disposable oracle's callable reference surface is `publish`, `revise`,
`withdraw_dependent`, `report_performed`, `verify`, `decide_artifact`, `resolve`,
`acknowledge_continuation`, and read-only `request`, `dependants`,
`list_requests`, and `watch`. These are owner-transaction test hooks, not
proposed wire operation names. Candidate adapters must preserve their
committed result/refusal behavior across generated clients and transports.
Fixture identities, repo/principal/run dependent links, and eligible evidence
references are seeded into the oracle's SQLite owner tables. The stub rejects
caller-provided role/workspace claims, requires link ownership (or the seeded
coordinator role), and binds evidence to request, current material digest and
outcome. It does not perform real authentication or evidence-source validation.
The event log is written in
the same SQLite transaction as each request mutation. The cursor/watch/query
hooks exercise bounded page sizes, replay, expiry, workspace isolation and
revocation. They are deliberately narrow test fixtures, not claims that the
full OA lifecycle has been modeled.

The tests use temporary file-backed SQLite databases, independent connections,
and close/reopen replay checks. This demonstrates replay after a database
connection/process-style reopen; it is not a power-loss or storage durability
qualification. Concurrent CAS is tested with two separate connections racing
on the same revision. Every request-changing transition in the fixture
increments its revision; material revision clears report, verification,
evidence and exact-artifact decision fields.

## Comparison procedure (not an architecture decision)

After EC-01 publishes its producer/consumer and payload inventory, instantiate
each candidate against the same SQLite owner oracle and the same captured
semantic transcript, reusing this oracle only as a falsifiable owner stub:

1. protobuf authored source with native gRPC and a separately qualified
   Connect/browser edge;
2. TypeSpec authored source emitted to OpenAPI/JSON Schema with HTTP and an
   explicitly cursor-bearing watch/read path;
3. direct OpenAPI/JSON Schema only if EC-00's shortlist says it is a serious
   lower-tooling alternative.

The record of comparison must report the precise compiler/plugin/runtime pins,
languages, transport boundaries and generated projections for each arm. Keep
one semantic case corpus and compare request/result/error meaning at the owner
boundary, including field presence, numeric range, unknown values and
authorization failures. Candidate adapters may translate, but may not add a
second owner, store or authority.

Before performance runs, EC-01 must supply representative payload classes and
sizes, concurrency and network conditions. A root-captured safe observation of
adjacent controlled owner/client traffic measured canonical operation argument
classes of 787 bytes (targeted continuation), 16 bytes (scoped item query), 716
bytes (work create), 9,611 bytes (exact-artifact proposal), and 1,941 bytes
(verification evidence), one sample each. It recorded no wire bytes,
concurrency, latency, or OA traffic. Treat these only as
candidate correctness/load fixture sizes. EC-01 must confirm which classes
represent actual estate consumers before any are adopted as the benchmark
matrix. Do not claim an OA distribution or invent payload sizes or performance
targets here. Run each arm with the same workloads, owner
transaction behavior, host, resources, concurrency and error injection. Record
raw run metadata and report p50/p95 latency, CPU/RSS, bytes, reconnect/replay
recovery, code-generation/build time, manual adapter code, added deployable
components, debugging effort and operator steps. Publish sample count,
warm-up method, confidence/variance and failed trials; no binary-speedup claim
without measurements. Correctness and security cases are pass/fail gates, not
weighted scoring items.

EC-00 must test: Python and TypeScript clients; Go/Rust only if EC-01 finds
consumers; old/new producer-consumer combinations; absent/zero/null/unknown;
integers beyond JavaScript's safe range; duplicate commands; concurrent CAS;
response loss after durable commit; expired cursor and replay; slow subscriber;
cross-tenant/revoked access; changed artifact meaning; browser/proxy behavior;
offline restore; and deterministic code generation. Include error/deadline and
cancellation semantics. A transport stream is not a durable event log.

Decision criteria and ADR should follow `docs/plans/agentops/2026-10-18-estate-contract-architecture-discovery.md`:
select a canonical authoring source and exact versions only after the candidate
arms pass identical semantic cases and the measured operational comparison.
The included owner stub is a disposable oracle only; it is not an OA schema,
public contract, production API, persistence recommendation or final transaction
design.

## First executable generated-client slice (18 October 2026)

This is a narrow correctness comparison for `publish` and `revise`, executed by
generated Python and TypeScript clients against the same temporary,
file-backed `OwnerOracle` database. It does not select A or C, model the full OA
lifecycle, qualify authentication, qualify a network/proxy path, or report
performance. The HTTP/protobuf bridge is a test-only loopback adapter that
delegates every accepted mutation to the existing oracle; it is not a proposed
Vuoro service or production API. The protobuf arm exercises generated protobuf
binary messages and service descriptors, not a gRPC transport. Native gRPC,
Connect, event replay between clients, and the broader decision gate remain to
be qualified.

The predeclared cases in this slice are:

- publish and revise through both Python and TypeScript generated clients;
- decimal-string request identifiers `9007199254740993` and
  `9007199254740995`, above JavaScript's safe integer limit, with persisted
  values checked byte-for-byte after client serialization;
- optional protobuf field presence: absent objective is refused while a
  present empty objective is accepted; absent optional `expected_revision` is
  refused and zero reaches the owner's stale-revision refusal;
- TypeSpec/OpenAPI optional and nullable `expectedRevision`: absent and `null`
  are refused before the owner call, zero reaches the owner's stale-revision
  refusal, and revision 1 succeeds; and
- optional nullable note semantics through both representations: absent,
  present empty string and explicit `null` reach the same persisted JSON
  shapes. The protobuf candidate uses a oneof with a `NullValue` arm to encode
  `null`; OpenAPI uses its optional nullable string field.

The `expectedRevision: null` case is exercised only in the OpenAPI arm because
the protobuf candidate models `expected_revision` as an optional `int64`, which
has absence and integer values but no scalar `null`. Both arms exercise absent,
zero and valid expected revisions.

The Python and TypeScript clients both exercise both candidate representations
against one actual SQLite oracle per test. This proves only the shown
transaction and serialization cases. In particular, the generated OpenAPI
TypeScript client represents `int64` revision values as JavaScript `number`;
tested revision numbers are small. Large revision counters are not qualified
by this slice. The request ID remains a decimal string in both arms so its
precision does not depend on JavaScript number behavior. Protobuf has absent
versus present scalar presence but no separate `null` scalar value; that
difference is kept explicit rather than papered over.

The executable generator inputs and outputs are under
`tests/experiments/ec00/candidates/` and `tests/experiments/ec00/generated/`.
`package-lock.json` and `uv.lock` pin package versions and transitive
artifacts. The verified local generator toolchain was Node 24.21.0 (binary
SHA-256 `f89c69d163e2686ed04cae19f5680668d4c1c64a072a70c1a6e50842a7a9e433`),
npm 11.19.0, protoc 36.2 (binary SHA-256
`2c089b2c3fdcac51b420e467f9594ad0176b396998d2b4571f93b9f2ad8ee953`),
TypeSpec compiler/OpenAPI emitter 1.17.0, Buf `protoc-gen-es` and protobuf
runtime 2.16.0, Hey API OpenAPI TypeScript generator 0.99.0 with TypeScript
5.9.3 (within its declared peer range), Python 3.13.16 with protobuf 7.36.2,
httpx 0.28.1 and `openapi-python-client` 0.29.1, managed with uv 0.13.0. The
two compiler binary hashes are deliberate environment pins; a different
binary must be reviewed and recorded as a toolchain change.

From the repository root, install the locked generators and runtime, then
verify generated output and run the full disposable experiment tests. Set the
two binary paths to files whose hashes match the recorded values:

```bash
export EC00_NODE=/path/to/node-24.21.0/bin/node
export EC00_PROTOC=/path/to/protoc-36.2/bin/protoc
export PATH="$(dirname "$EC00_NODE"):$PATH"
npm ci --prefix tests/experiments/ec00/candidates --ignore-scripts --no-audit --no-fund
uv sync --project tests/experiments/ec00/candidates --locked
uv run --project tests/experiments/ec00/candidates --locked \
  python scripts/experiments/ec00/generate_candidates.py
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p 'test_*.py' -v
```

The generator check also runs `tsc --project
tests/experiments/ec00/candidates/tsconfig.json` with the locked TypeScript
compiler before comparing regenerated output.

`generate_candidates.py --write` deliberately refreshes only the disposable
generated-artifact directory. The default command regenerates to a temporary
directory and fails on any difference. The integration tests bind only an
ephemeral loopback port, seed identities and links in a temporary database,
and use no network credentials.

The TypeSpec-to-OpenAPI mapping is documented by the
[TypeSpec OpenAPI emitter](https://typespec.io/docs/emitters/openapi3/openapi/).
Python client generation uses the
[openapi-python-client file-input workflow](https://github.com/openapi-generators/openapi-python-client),
and TypeScript client generation uses
[Hey API's OpenAPI TypeScript generator](https://heyapi.dev/docs/openapi/typescript/get-started/).
The protobuf messages are generated with `protoc`; the official
[Python generated-code guide](https://protobuf.dev/reference/python/python-generated/)
describes the compiler's Python output.

## Offline check

Run with standard library only:

```bash
python -m unittest discover -s tests/experiments/ec00 -p 'test_*.py' -v
```
