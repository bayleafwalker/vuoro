# EC-00 disposable qualification workspace

## Versioned v3 unknown-value qualification (23 October 2026)

The isolated v3 fixture passes its bounded unknown-value correctness gates while
retaining the frozen v1/v2 artifacts and the negative v2 findings below. This is
not production adoption or EC-00 closure.

`candidates/typespec/unknown-values-v3.tsp` is the sole v3 schema source. Pinned
TypeSpec compiler/HTTP/OpenAPI/JSON Schema emitters 1.17.0 independently produce
explicit OpenAPI 3.0.0 for four generated SDKs and JSON Schema 2020-12 for runtime
validation. The actual JSON Schema emitter option is `bundleId`. Explicit
`...Record<unknown>` response metadata remains open in both projections; sealed
command, nested body and signed-artifact inputs refuse unknown fields. One
canonical decimal-text scalar bounds exact counters to signed int64, including
refusal of trailing newlines. Lifecycle state remains a closed enum.

The generator derives the operation/schema manifest from emitted operation IDs
and named schemas. It generates the TypeScript attachment to the existing SDK
`responseValidator` hook using Ajv 8.20.0. The Python boundary uses jsonschema
4.26.0 and the same finite supported-feature gate as generation. Both runtimes
use generated schemas without coercion, injected defaults or property removal;
remote/dynamic references, fragment references and unsupported schema features refuse.
Only declared named local resource IDs are admitted, at generation and runtime load. Content
encoding metadata does not replace owner byte/signature verification. The
bounded publish/revise/readRequest/readArtifact adapter calls the unchanged
`OwnerOracle`; it adds no domain state machine or production writer.

Root-owned offline runs passed 13 emitter/schema/owner tests (21.711 s) and four
cross-client tests (19.665 s). The latter recorded 24 actual HTTP response rows
across Python, TypeScript, Go and Rust: baseline, additive top metadata, additive
nested metadata, unknown lifecycle, absent note and empty note. Baseline note is
explicit null. All four tolerate safe additive metadata, reject unknown lifecycle
meaning, retain exact large counters and distinguish absent/null/empty values.
All 24 artifact reads preserve original bytes, envelope, signature and digest;
all read owner snapshots remain unchanged. There were zero findings and zero
could-not-check rows.

Fourteen raw HTTP command cases retain one active positive mutation and refuse
unknown top/nested command fields, validly signed changed envelope meaning, and
missing/null/numeric/noncanonical/out-of-range counters without owner effects.
Independent guards reject silent effects and inert positives. Actual SDK
validation omission accepts the unknown HTTP 200 lifecycle response and is
rejected by the qualification guard, proving that the attached validator matters.
A clean regeneration and separate request-schema, result-schema, attachment and
SDK-output mutations exercise byte-drift refusal. Strict TypeScript noEmit
checking passes for the generated attachment and new runner with the already
pinned Node ambient types explicitly selected.

Reproduce the focused correctness gates in the prepared locked environment:

```bash
nix develop --offline ./tests/experiments/ec00/candidates/toolchain --command \
  uv run --offline --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p '*_v3_checks.py' -v
```

`EC00_UNKNOWN_V3_REPORT` optionally names the v3 matrix output. The source,
generated outputs and candidate locks are frozen for independent review. This
qualifies four-language responses and raw command fixtures, not four-language
SDK mutation serialization or universal JSON Schema enforcement by native
SDKs. After a rejecting fragment-reference repair, the nine emitter/schema/SDK
and generation-drift checks passed again (14.702 s). Existing v1/v2 regressions
passed all 56 tests (143.684 s), the ordinary candidate TypeScript project passed,
and four built wheels passed all six repository distribution-boundary tests.
The wider dependency supply, ingress, performance/economics and estate
qualification remain separate.

## Selected-chain unknown-value findings (23 October 2026)

`unknown_value_checks.py` characterizes the frozen TypeSpec/OpenAPI v2 clients
against the unchanged persisted `OwnerOracle`. The default focused qualification
gate deliberately fails: the selected unknown-value policy is **not qualified**.
Neither the generated clients, IDL, frozen outputs nor owner rules were repaired
or relaxed to produce a passing result.

The locked runtime ran all 16 response-decoder cases (four variants in Python,
TypeScript, Go and Rust), seven raw command/control cases and two independent
policy-guard falsifiers. Every generated read reached the fixture as HTTP 200;
there were no missing-runtime skips. The guard falsifiers passed. The policy
gate retained these four findings:

| Case | Actual observation | Disposition |
|---|---|---|
| Additive top-level response metadata, Go | Generated decoder refuses the response. | Forward-compatible metadata handling remains unqualified. |
| Additive nested response-body metadata, Go | Generated decoder refuses the response. | Same gap at the nested response boundary. |
| Unknown lifecycle state, TypeScript | Generated SDK returns a decoded result containing `future-success`. | Runtime response validation is required before treating that result as known lifecycle meaning. No actual work acceptance was exercised. |
| Unknown command field | Existing HTTP fixture returns 200 and commits one revision/event/idempotency result while dropping the unknown field. | Command unknown-field refusal is not implemented by this candidate. |

Baseline reads pass in all four languages, including an exact decimal revision
above the JavaScript safe-integer range and distinct empty/null body values.
Python and Rust tolerate both additive metadata variants and reject the unknown
lifecycle state; Go rejects the unknown state as well. All 16 generated artifact
reads retain original bytes, envelope bytes, signature and digest exactly. Owner
request/event/idempotency/dependant/artifact snapshots stay unchanged for every
read. The known command positive control does commit, so the no-effect checks do
not succeed merely because the fixture never invokes its owner.

Validly signed envelopes containing an unknown nested field or unknown authority
intent are refused with HTTP 409 and no owner effect. Missing and null mutation
revisions return 422; zero returns stale-revision 409, all without owner changes.
The command schema is flat, so ordinary nested command-object coverage is absent;
the applicable nested input tested here is the signed envelope. Command probes
use raw HTTP because the frozen generated request models do not all support
constructing an unknown field. Four-language coverage applies to generated
response decoders, not to command serialization.

Run the existing locked toolchain, with caches prepared independently:

```bash
nix develop --offline ./tests/experiments/ec00/candidates/toolchain --command \
  uv run --offline --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 \
  -p unknown_value_checks.py -v
```

`EC00_UNKNOWN_REPORT` optionally names a private JSON output containing the matrix,
raw fixture projections, owner snapshot digests, findings and toolchain paths.
The existing Go/Rust counter runners use `EC00_UNKNOWN_VALUES=1` for this mode;
their original counter mode is unchanged. TypeScript uses
`candidates/run_unknown_values.ts`. No generator/dependency change is included.
The focused command currently exits 1 for the four findings above. These are
disposable loopback correctness observations, with no production, ingress,
performance or general semantic-competence claim. EC-00 remains open.

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
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p 'candidate_slice_checks.py' -v
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

The wire-compatibility fixture preserves the generated Python and TypeScript
publish/revise clients and disposable HTTP owner from commit
`5ff5d82345d2b668ef4495656547a04877f1c275`. Its source files and local runner
files are SHA-256 pinned in
`tests/experiments/ec00/compat/merge-base-5ff5/manifest.json`; it is not
regenerated from the new schemas. The compatibility test runs old Python and
TypeScript clients against the expanded server, and current Python and
TypeScript clients against the frozen server, for both protobuf-over-HTTP and
TypeSpec/OpenAPI publish/revise. The frozen server does not implement native
gRPC, so current gRPC-to-old-server compatibility is not claimed. This checks
only the common publish/revise wire operations, not full-lifecycle old/new
compatibility.

The additive protobuf fields retain old-client wire interpretation in these
tests, but language source compatibility differs: Rust struct literals for
`PublishRequest` must add `..Default::default()` when generated fields are
added. The Rust fixture uses that initializer explicitly. This is evidence of
source-level construction friction, separate from the wire compatibility
result.

## Second executable generated-client slice (18 October 2026)

This bounded extension adds real Go and Rust generated clients to the same
temporary SQLite `OwnerOracle` tests. The protobuf arm now uses generated
Python, Go and Rust gRPC clients against a native gRPC loopback server; the
TypeSpec/OpenAPI arm uses generated Python, TypeScript, Go and Rust HTTP/JSON
clients against the existing test-only HTTP adapter. Both adapters delegate to
the same owner methods and temporary database in each test. They do not store
state of their own. Go/Rust are included here as comparison clients; this slice
does not claim they are deployed estate consumers.

The additional predeclared vectors cover:

- Go and Rust publish/revise over generated protobuf/gRPC and generated
  TypeSpec/OpenAPI clients, with decimal-string identifiers above
  `Number.MAX_SAFE_INTEGER` checked against the committed owner rows;
- absent, explicit `null`, and empty-string optional notes through each arm,
  persisted and checked as distinct JSON values;
- missing `expected_revision` rejected before the protobuf owner call, zero
  reaching the stale-CAS refusal, and revision 1 succeeding; and
- missing, null, zero, and revision 1 `expectedRevision` cases through both
  generated OpenAPI clients. Protobuf has no scalar null case for this field.

The protobuf clients use native HTTP/2 gRPC on a test-only loopback server;
the Go/Rust protobuf methods are not the earlier binary-message-over-HTTP
exercise. The Python gRPC client also calls the generated service stub. This
only qualifies the client code and the loopback protocol boundary. It does not
qualify TLS, authentication, a network or proxy path, error behavior under
production middleware, retries, streaming, or deployment. HTTP/OpenAPI and
gRPC status codes are checked at their respective client boundaries, while the
same persisted owner result/refusal remains the comparison oracle.

The OpenAPI Go and Rust clients are generated by OpenAPI Generator 7.26.0 from
the TypeSpec-emitted OpenAPI document. The generated Rust protobuf service code
is built from the committed `.proto` by `tonic-prost-build` 0.14.6 and the
locked Cargo dependencies. Go protobuf message/service code is generated with
`protoc-gen-go` 1.36.12 and `protoc-gen-go-grpc` 1.6.2. The generated Python
gRPC stub is emitted by the pinned `grpcio-tools` 1.84.0 (bundled `libprotoc
35.1`); protobuf Python messages and Go/TypeScript bindings are emitted by the
existing pinned `protoc` 36.2. Nix `flake.lock` fixes the compiler/tool package
set to Nixpkgs `f20cd90e28fe0403873f473b29e42662cd86e8c4` with its recorded
NAR hash. It provides Go 1.26.8, Rust/Cargo 1.98.1/1.98.0, JDK 21.0.12.1,
OpenAPI Generator 7.26.0, Node 24.21.0 and protoc 36.2. Go modules are pinned
in `go.mod`/`go.sum`; Rust packages are pinned in `rust-client/Cargo.lock`.
The Go OpenAPI generator's placeholder module path is normalized to the fixed
local module name during generation; the script refuses if that placeholder
changes.

From the repository root, enter the locked toolchain shell. Run the remaining
commands in that shell to install the Node and Python generators and verify
generated output:

```bash
nix develop ./tests/experiments/ec00/candidates/toolchain
```

```bash
npm ci --prefix tests/experiments/ec00/candidates --ignore-scripts --no-audit --no-fund
uv sync --project tests/experiments/ec00/candidates --locked
uv run --project tests/experiments/ec00/candidates --locked \
  python scripts/experiments/ec00/generate_candidates.py
```

The EC-00-specific executable checks are separate from normal repository test
collection and use loopback only:

```bash
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p 'test_*.py' -v
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p 'candidate_slice_checks.py' -v
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p 'multilang_slice_checks.py' -v
(cd tests/experiments/ec00 && go test ./...)
cargo check --manifest-path tests/experiments/ec00/candidates/rust-client/Cargo.toml --locked
```

The Python, TypeScript, Go and Rust cases in this document remain a narrow
correctness slice. They do not satisfy EC-00's full lifecycle, compatibility,
replay, authorization, cancellation, browser/proxy, offline-restore or payload
matrix; they make no architecture selection or performance claim. The full
ADR remains gated on those shared cases and representative EC-01 workload
evidence.

## Third executable owner-lifecycle slice (18 October 2026)

This test-only extension exercises the predeclared publish/revise/withdraw,
performed report, evidence verification, exact-artifact decision, resolve,
per-dependent acknowledgement, bounded reads/watch, revocation and post-commit
replay cases through the same file-backed SQLite owner oracle. Generated Python
TypeSpec/OpenAPI and native protobuf/gRPC clients run the complete lifecycle;
generated TypeScript clients exercise artifact publication/readback and initial
withdraw/report transitions through both representations. TypeScript protobuf
messages use binary protobuf over the test-only HTTP bridge; the native gRPC
qualification is the Python generated protobuf client (at this slice's landing,
the Go/Rust clients were publish/revise-only; see the follow-up below). The
clients never supply role or workspace claims:
the disposable server binds one seeded principal for the whole client process.

Signed artifacts retain the exact binary artifact, canonical signed-envelope
bytes and Ed25519 signature in SQLite BLOBs. The versioned test-only envelope
binds domain `vuoro-ec00-artifact-decision/v1`, intent `accept`, key id,
workspace, request id, material revision, material digest and artifact digest.
The fixture keeps aggregate CAS/event `revision` separate from
`material_revision`: publication starts both at 1, while each revise advances
both, including a same-body revise. The artifact read route and its result name
`material_revision` explicitly; mutation requests continue to use
`expected_revision` for aggregate CAS. Report, verify, and artifact-decision events
advance only the aggregate revision. This permits the declared publish → report
→ verify → artifact-decision → resolve order; the decision uses the current
aggregate expected revision and the signed artifact for the current material
revision. The exact binary, envelope bytes and signature remain historical, but
a prior material revision cannot authorize a decision after any revise. The deterministic
fixture key is public test material only; it is not a product key or a
production cryptography design. `cryptography==50.0.0` is pinned in the
disposable Python lock and checked by the generator validation.

The test-only HTTP adapter carries generated TypeSpec calls and protobuf
binary messages to the same SQLite owner. The separate gRPC server delegates
generated protobuf service methods directly to that owner. Injected response
loss occurs only after the owner transaction commits; retrying the same
idempotency key checks the persisted result. Cursor, role, workspace and
revocation checks are owner-boundary test cases. They do not qualify real
authentication, TLS, network middleware, storage power-loss durability,
production evidence sources, browser/proxy behavior or a deployed service.
The LC07 slice accepts unknown JSON/protobuf fields without adding them to the
owner projection and rejects an unrecognized protobuf `NullValue` enum number
rather than collapsing it into null. LC08 here covers frozen Python and
TypeScript clients for common OpenAPI/protobuf-over-HTTP publish/revise
operations. The separate 59fa compatibility fixture below now tests the
generated Python, Go and Rust protobuf clients in both directions over native
gRPC. The matrix is limited to common Publish/Revise; it does not cover newly
added RPCs.

## Typed-read and Go/Rust lifecycle slice (18 October 2026)

This follow-up replaces opaque JSON-string read results with typed request,
dependent, artifact, list and watch projections in both candidate schemas.
Generated Go and Rust OpenAPI/HTTP clients and generated Go and Rust native
protobuf/gRPC clients now exercise the same persisted publish → withdraw a
separate third dependent → report → verify → decide artifact → resolve →
acknowledge the two still-active dependents sequence. Each transport reads the
persisted request, dependants, signed artifact and bounded list/watch pages.
The shared event golden is identical across these four Go/Rust paths. The
earlier Python lifecycle transcript remains its predeclared seven-mutation
case with a separate withdrawal case; it is not represented as the same event
sequence.

Both Go and Rust also read a seeded request with `revision`,
`material_revision`, and `created_seq` above JavaScript's exact integer range.
OpenAPI/TypeSpec encodes these read-only `int64` values as decimal strings;
native protobuf uses `int64`. The clients compare exact decimal values and
exact stored binary artifact, envelope and signature bytes. The valid empty
artifact payload case uses `original_bytes = b""` with nonempty signed context
and a verified Ed25519 signature; an absent bundle, a partial bundle, or a
bundle with empty digest/key/envelope/signature metadata is invalid. JSON body
unknown fields are not preserved by the owner projection and
are not claimed as a typed-read capability. Existing HTTP frozen-client LC08
coverage remains. A separate focused test runs the generated 59fa Python gRPC
stub and frozen 59fa Go/Rust clients against the current service, then runs
current generated Python, Go and Rust clients against the frozen 59fa service.
The Go client, owner and protobuf bindings are source files copied byte-for-byte
from 59fa and hash checked. The baseline did not check in generated Rust gRPC
bindings; its frozen Rust client and `build.rs` compile the frozen
`ec00/candidates/protobuf/oa.v1.proto` using the frozen Cargo manifest and lockfile,
not the current proto or a checked-in binary. The compatibility test pins the fixture
manifest digest independently of its per-file hashes, so coordinated fixture
and manifest edits are rejected. These checks cover only common Publish/Revise.
The test also confirms that current `ReadRequest` receives `UNIMPLEMENTED`
from the old service; newly added RPCs are not otherwise tested here.

This typed-read replacement intentionally changes the payload on existing v1
HTTP and RPC read methods from `JsonResult { json: string }` (the a94cf67
candidate contract) to typed response objects, without version negotiation.
The negative fidelity test demonstrates the protobuf wire hazard: an old
`JsonResult` decoder reads the new `RequestRead.request_id` field as its
`json` field. This fixture's numeric identifier then parses as a JSON integer,
not the prior read object, so the mismatch can be silent. Frozen publish/revise
compatibility does not cover this read break. This is an experimental schema
change only; safe production migration needs an explicit
compatibility/versioning decision outside this slice. Also, the `>2^53` exact
integer evidence covers read projections only: mutation `expectedRevision`
remains a JSON number in this candidate and is not qualified above that range.

These generated-client cases remain test-only correctness evidence. They do
not qualify the complete native gRPC compatibility matrix or the complete EC-00
matrix, browser/proxy behavior, production authentication or performance, and
do not select an architecture.

Run the lifecycle tests explicitly inside the pinned EC-00 toolchain shell and
a loopback-permitting test environment. The shell exports Node, Go, Cargo,
protoc and the protobuf include path:

```bash
uv run --project tests/experiments/ec00/candidates --locked \
  python tests/experiments/ec00/test_owner_oracle.py
nix develop ./tests/experiments/ec00/candidates/toolchain --command \
  uv run --project tests/experiments/ec00/candidates --locked \
  python tests/experiments/ec00/lifecycle_slice_checks.py \
  LifecycleSliceTests.test_frozen_59fa_and_current_generated_grpc_clients_interoperate
nix develop ./tests/experiments/ec00/candidates/toolchain --command \
  uv run --project tests/experiments/ec00/candidates --locked \
  python tests/experiments/ec00/lifecycle_slice_checks.py \
  LifecycleSliceTests.test_frozen_59fa_go_rust_clients_against_current_grpc_service
```

This slice is still a correctness experiment, not an architecture decision.
Old/new native gRPC compatibility beyond the common Python/Go/Rust
Publish/Revise surface, RPC cancellation/deadline behavior, browser/proxy and
offline restore cases, representative workload/performance comparison, and EC-01
producer/consumer evidence remain open gates before the ADR.

## Offline check

Run the owner-oracle tests with the locked candidate project; they use the
pinned test-only Ed25519 library, but make no network calls and read no runtime
credentials. The generated-client integration test has a non-default pytest
filename so regular workspace test collection does not import optional EC-00
generator runtimes; run it explicitly with the locked candidate project as
shown above.

```bash
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p 'test_owner_oracle.py' -v
```


## Generated Python transport-failure slice (18 October 2026)

`transport_failure_checks.py` runs twelve cases: generated Python native gRPC
and generated TypeSpec/OpenAPI HTTP clients, each with deadline, explicit
client cancellation, and injected response unavailability before owner
invocation or after the actual SQLite commit. Each case has its own file-backed
database. A test-local event gate waits at the owner-call boundary; independent
SQLite connections inspect request, event and idempotency rows while the call
is paused and again after it finishes. No owner implementation or public
contract gains a cancellation operation.

The gRPC deadline surfaces `DEADLINE_EXCEEDED`, cancellation raises
`FutureCancelledError`, and injected response loss surfaces `UNAVAILABLE`.
The HTTP equivalents are a configured read timeout (`ReadTimeout`), cancelled
async generated-client task (`CancelledError`), and a connection closed without
an HTTP response (`RemoteProtocolError`). The unavailability fault is a harness
injection, not a demonstrated packet loss, proxy failure or server crash.

Both current test adapters allow an already dispatched handler to finish when
its client times out or cancels. The before-invocation gate first proves zero
owner rows; after cancellation/deadline and gate release, the handler commits
exactly one request, event and idempotency result. The after-commit gate proves
those three rows already exist before the client failure. Cancellation therefore
does **not** qualify a no-effect outcome, owner cancellation or rollback. A
pre-invocation unavailability injection prevents the owner call entirely; the
same-key retry then creates the one logical result. For every case, two same-key
replays return revision 1 and a changed-body replay refuses without altering the
single persisted result. The request ID above JavaScript's safe integer limit
and exact original objective remain intact.

Run with the locked candidate Python environment:

```bash
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p transport_failure_checks.py -v
```

The dedicated `ec00-transport-failures` CI job executes these cases. This is a
Python/loopback/publish comparison only. Go/Rust/TypeScript cancellation,
server-enforced deadlines, admission refusal after disconnect, commit-time
cancellation, real authentication, TLS/proxy/browser behavior and production
retry policy remain unqualified. It supplies no architecture or performance
conclusion. Existing lifecycle and frozen old/new compatibility results retain
their separate scope.


## Generated Python unary watch replay slice (18 October 2026)

`watch_replay_checks.py` compares native gRPC `Watch` and the generated OpenAPI
GET `/v1/events` client against the same temporary file-backed owner and seeded
principal. These are bounded unary pages, not streams or subscriptions. The
checks compare complete semantic pages, cursor bytes, sequence order and owner
request/event/idempotency rows using independent SQLite connections.

Both clients replay identical cursors after reconnect and four injected lost
responses (before/after the owner read in each transport). Eight additional
cases time out or explicitly cancel generated-client calls at those read
boundaries; retrying the same cursor returns the same page and leaves owner
rows unchanged. Delayed polling with a writer appending between polls drains
all six persisted events in order, at most two per call, then returns an empty
page retaining its cursor. This tests a consumer that pauses between polls;
it supplies no evidence about streaming queues or network backpressure.

The refusal comparison covers oversized limits, tampered cursors, a list cursor
used as a watch cursor, other principal/workspace, retention expiry and revoked
principal. Each refusal preserves complete request/event/idempotency rows.
HTTP refusals return 409 and the gRPC test adapter now explicitly maps owner
`Conflict` to `ABORTED`; read-response loss maps to `UNAVAILABLE` or a closed
HTTP response. The original adapter failed these comparisons with gRPC
`UNKNOWN`; no production service or owner contract changed. Fixture identity
seeding is not authentication qualification. A separate negative comparison records a limit semantic difference: omitted
limits return two events in both arms; explicit gRPC scalar zero also defaults
to 2, while explicit HTTP zero reaches the owner's refusal. This is an observed
candidate mismatch, not a parity pass or a selected defaulting contract.

Run the locked candidate environment with unittest discovery pattern
`watch_replay_checks.py`; the dedicated EC00 CI job executes it beside the
transport-failure slice. Real network/proxy/browser/TLS/authentication paths,
other language watch clients, streaming and subscriber backpressure remain
unqualified. These results do not select an architecture or report performance.


## Large mutation counters: positive and negative qualification (18 October 2026)

`large_cas_checks.py` exercises actual owner CAS at synthetic counter 1,
9007199254740995 and 9007199254740996, using independent file-backed SQLite
databases for generated OpenAPI Python, native gRPC Python, OpenAPI TypeScript
and protobuf TypeScript clients. TypeScript protobuf uses generated binary
messages over the existing test-only HTTP bridge; it is not native gRPC.
The fixture seeds counters directly and does not claim trillions of observed
transitions, natural counter growth, or additional concurrency qualification.

The twelve subcases check exact committed request/material revisions, objective,
appended event fields, preserved prior events and the stored idempotency result.
The OpenAPI TypeScript runner captures the actual HTTP request and response text;
Python decodes that text without JavaScript numeric rounding. Its findings are:

- With expected revision 9007199254740995, the generated TypeScript numeric
  input serializes as 9007199254740996. The owner refuses stale CAS (409), and
  all request/event/idempotency rows remain unchanged.
- With the exact representable even input 9007199254740996, CAS succeeds and
  the owner commits 9007199254740997. The HTTP response contains that exact
  integer, but the generated TypeScript numeric result reports 9007199254740996.
  A committed effect therefore cannot be inferred correctly from that parsed
  revision alone.
- Python OpenAPI and Python gRPC preserve both tested large expected revisions
  and results; TypeScript protobuf preserves them through `bigint` and binary
  encoding. All four arms pass the counter-1 control case.

These are explicit negative findings for the current OpenAPI TypeScript numeric
contract, not a full-int64 parity pass. The existing decimal-string typed reads
do not repair mutation input or result precision. A future contract decision
must resolve this limitation (for example with a consistently generated decimal
representation) and qualify compatibility; this slice changes no schema,
production contract, generator projection or selected architecture.

The dedicated `ec00-large-cas` CI job installs Node 24.21.0, locked npm tools and
the locked Python environment, type-checks both TypeScript runners and runs the
comparison. Local execution uses the pinned Nix toolchain and the same locked
packages. Run unittest discovery with pattern `large_cas_checks.py` after
installing those tools and setting `EC00_NODE` to the pinned executable. There
are no performance, proxy, browser or production qualification claims here.


## Versioned decimal mutation counters (18 October 2026)

The TypeSpec challenger now has an explicit experimental `/v2` projection. Its
authored source imports the v1 models, clones them into the v2 namespace, and
applies decimal-string encoding to every mutation expected revision and mutation
result revision. It declares nonnegative counter bounds and canonical decimal
meaning beside that encoding. V1 stays available with its original numeric wire
contract, including the precision limitations measured in the previous slice.
This remains a disposable comparison input; no estate API, selected IDL,
production package, migration window or architecture ADR changes here.

The test-only v2 codec accepts canonical nonnegative decimal text within signed
int64 range and rejects numeric JSON, signs, whitespace, leading zeros, fractions,
exponents and overflow before owner invocation. It converts the accepted value
for the same existing `OwnerOracle`; that owner still performs CAS, authorization,
idempotency, event transactions and signed-artifact validation. The codec adds
an explicit wire version and keeps one authoritative owner/ledger. Generated
client types carry strings; this is not a claim of complete generated runtime
validation for arbitrary hostile responses.

Executable checks cover:

- Sixteen isolated generated-client cases: Python, TypeScript, Go and Rust,
  each at counters 1, 9007199254740995, 9007199254740996 and
  9223372036854775806. All preserve exact input/result text, replay one key to
  the same result, and refuse a changed body. Independent SQLite reads verify
  one transition/event/idempotency result. The last fixture increments to signed
  int64 maximum. Counters are seeded directly; natural counter growth,
  increment beyond maximum and additional concurrency qualification are outside
  this slice.
- Actual TypeScript request/response text carries decimal strings. The earlier
  v1 numeric failure remains reproducible. Invalid v2 types, lexemes and overflow,
  absent/null revisions and stale zero/maximum values leave owner rows unchanged.
- A generated Python v2 lifecycle publishes an exact signed artifact, withdraws
  one shared dependent, reports, verifies, records the artifact decision,
  resolves and acknowledges separate remaining dependants. Seven induced
  post-commit response losses reconcile through same-key replay. Partial
  acknowledgement is observed before the second dependant continues. Original
  artifact, envelope, signature, digest and material meaning remain intact.
- V1 numeric and v2 decimal routes coexist. Generated Python commands replay
  the same semantic command/key across versions in both directions at counters
  1 and 9007199254740995, preserving one owner transition and rejecting changed
  meaning. Frozen v1 Python/TypeScript clients
  interoperate with the versioned server; the complete ten-case v1 lifecycle
  regression also passes through it, including the frozen native Go/Rust cases.
  A v2 client against a v1-only server gets 404 and does not downgrade or mutate.

The generated OpenAPI and Python/TypeScript/Go/Rust clients live under
`generated/typespec-v2`. `generate_versioned_counters.py` first verifies the
existing v1 artifacts and pinned toolchain, compiles both services, requires the
emitted v1 OpenAPI to remain byte-identical, then generates/checks only the v2
subtree. Actual falsifiers reject altered v2 output and a versioned source that
attempts to change v1 wire bytes. Generated files have no independently edited
schema bodies. The Go module and Rust consumer lock retain the existing external
dependency versions; Rust adds the generated local v2 crate.

Use the locked Nix toolchain and installed locked npm/Python tools:

```bash
uv run --project tests/experiments/ec00/candidates --locked \
  python scripts/experiments/ec00/generate_versioned_counters.py
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p versioned_counter_checks.py -v
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p versioned_native_checks.py -v
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p versioned_generation_checks.py -v
```

The generator's `--write` refreshes its isolated v2 targets. Hosted CI runs the
Python/TypeScript checks and type checking beside the v1 counter comparison;
Go/Rust execution and complete regeneration/falsifiers are verified locally with
the pinned Nix toolchain. This establishes a tested representation repair for
one candidate. Protobuf zero/absent limit behavior, native browser/Connect/proxy
paths, TLS/authentication and representative workload/performance comparison
remain open before the final ADR and foundation implementation.

## Explicit read presence (18 October 2026)

The zero/absent discrepancy above is now addressed by separate experimental
`PageRequestV2`, `ListRequestsV2` and `WatchV2` identities. Both cursor and limit
have protobuf presence. An absent limit defaults to two; explicit zero,
negative or oversized limits are owner refusals. An absent cursor starts at the
beginning; an explicit empty cursor is refused. The existing `PageRequest`,
`ListRequests` and `Watch` retain their original zero/default and empty/reset
behavior. These names remain in the disposable experiment's service namespace;
they do not decide estate API version policy or the final IDL.

Seven primary checks compare generated Python native gRPC and OpenAPI `/v2`
clients, plus TypeScript generated protobuf messages over binary HTTP and the
generated OpenAPI `/v2` client. TypeScript binary HTTP is a test bridge, not
Connect, browser-native gRPC or a proxy qualification. Both read operations
exercise absent, explicit zero, empty, one, negative and oversized inputs with
actual serialized presence. Python checks ordered complete request/event fields
against SQLite, bounded reconnect and identical-cursor replay after reopening
the file, MAC/kind/principal/workspace/retention refusals, revoked identity,
response loss before/after owner reads and unchanged request/event/idempotency
rows. Malformed protobuf is rejected before owner invocation. A deliberately
broken new handler that restores legacy defaulting is detected for both
operations and both zero/empty cases.

A separate native Go/Rust check runs the same twelve input cases per language,
checks generated request bytes/presence, native status codes, exact generated
response messages against Python and unchanged owner rows. It does not extend
the Python reconnect/loss histories to every language. These native builds run
locally in the locked Nix toolchain; hosted CI runs the seven primary checks and
TypeScript type checking.

The frozen old server and Python client in `compat/read-server-5d13` use six
source/binding files byte-identical to landed commit
`5d13baf05b1d90348c57481cb1cfc35a620cf615`, with pinned source/runner hashes.
They run in separate processes with isolated import paths. Old clients retain
identical legacy response bytes against old and new servers, including explicit
empty/zero requests. New RPC calls to the old server return `UNIMPLEMENTED`;
there is no automatic downgrade. Both direct fixture drift and coordinated
fixture/manifest rehashing are rejected by the fixed manifest pin. These are
loopback experiment fixtures, not a production rolling-upgrade deployment.

Inside the pinned toolchain:

```bash
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p read_presence_checks.py -v
uv run --project tests/experiments/ec00/candidates --locked \
  python -m unittest discover -s tests/experiments/ec00 -p read_presence_native_checks.py -v
```

Legacy lifecycle and versioned counter checks remain required. Browser/Connect,
proxy/TLS/authentication, representative workloads and operational/performance
comparison remain open before an architecture decision or foundation rollout.

## Actual Chromium and Connect paths (18 October 2026)

The browser comparison now runs generated clients in actual headless Chromium
155.0.8059.79 from the locked Nix input, driven by Playwright 1.64.0. It compares
five isolated arms against the same existing SQLite-backed owner: direct Python
Connect ASGI in JSON and binary modes; a thin TypeScript Connect edge forwarding
to the existing Python native gRPC adapter in JSON and binary modes; and the
generated TypeSpec/OpenAPI `/v2` HTTP client. All arms pass through the same
loopback HTTP proxy and separately hosted browser origin. The edge forwards
only idempotency metadata, deadline and cancellation options; it holds no domain
state, identity selection or additional owner logic. Direct Python calls reuse
the existing projection and owner call without a gRPC forwarding hop.

Python Connect runtime and its local generator are pinned at 0.12.1, Uvicorn at
0.54.0, Connect ECMAScript runtimes at 2.2.0, esbuild at 0.28.2 and Node at
24.21.0. Existing protobuf, protoc and language pins remain unchanged.
`generate_connect.py` uses the local generator with `protobuf=google`, requires
its emitted Google Python messages to match the existing generated bytes
exactly, and packages those same messages with the generated Connect service.
The local pipeline normalizes the plugin's trailing whitespace deterministically;
generated files are not hand-edited. It writes only its isolated generated
subtree. Default verification and two
actual falsifiers reject generated-service drift and a disagreement with the
canonical generated protobuf bytes. No hosted code generator is required.

Six browser test groups cover:

- All five arms perform the complete eight-transition signed-artifact lifecycle
  with three shared dependants, one withdrawal, independent report/verification/
  artifact decision, partial acknowledgement and the second dependant's distinct
  continuation. Seven commands per lifecycle replay without additional events.
  Persisted and browser-read original bytes, envelope, signature and digest
  retain their meaning. Altering a signature before publication is refused
  without request, artifact, event or idempotency effects.
- Each arm performs exact CAS at synthetic revision 9007199254740995, preserves
  the next revision in the actual JSON/protobuf proxy bytes and browser result,
  reconciles same-key replay and refuses changed meaning. Independent SQLite
  rows verify one transition. This is not natural counter growth or a benchmark.
- Both paginated read operations preserve absent/zero/empty/bounds semantics,
  compare the first two items and the next one against complete persisted
  fields, and replay the same cursor. Principal/workspace mismatch and revoked
  fixture identity are refused with unchanged request/event/idempotency rows.
  Actual allowed preflights and denied foreign-origin preflights are observed.
- Five post-commit response losses reconcile by same-key replay. Twenty browser
  deadline/cancel cases pause a real owner call before invocation or after its
  committed return. A browser abandoning its wait does not establish rollback:
  after the handler is released, the owner can commit and replay returns the
  one committed result. Native Connect error codes and native browser HTTP
  abort/timeout errors are checked separately. This does not prove that this
  buffering test proxy propagates every downstream disconnect upstream.

The generated Python Connect client also runs eight uncompressed unary cases:
both direct/edge compositions and JSON/binary codecs at revisions 1 and
9007199254740995. Exact results, same-key replay, changed-body refusal and one
persisted transition are checked. This does not qualify its gzip defaults or
extend every browser failure history to the Python client.

An initial reference-WSGI-server harness passed the signed lifecycle but timed
out on Python Connect error/empty-body cases. Inspection points to raw WSGI
input EOF/drain handling in that harness. That failed run is retained as
host-persistent supporting evidence; **WSGI is not qualified**. The separately
run ASGI/Uvicorn configuration passes these cases. This is a runtime composition
result, not a claim that every Python Connect server has the same behavior.

Hosted CI runs the actual browser suite, type checking, local Connect generation
and both generator falsifiers in the pinned Nix toolchain. The earlier native
read, signed lifecycle and versioned counter suites remain regressions. Run:

```bash
nix develop ./tests/experiments/ec00/candidates/toolchain --command bash -euc '
  npm ci --prefix tests/experiments/ec00/candidates --ignore-scripts --no-audit --no-fund
  uv run --project tests/experiments/ec00/candidates --locked python scripts/experiments/ec00/generate_connect.py
  uv run --project tests/experiments/ec00/candidates --locked python -m unittest discover -s tests/experiments/ec00 -p connect_browser_checks.py -v
'
```

All identities are predetermined test fixtures; CORS is not authentication.
This qualifies these unary Chromium/loopback compositions only. Real production
identity propagation, TLS verification, deployed ingress/proxy behavior, browser
and native streaming/backpressure, other browser/runtime versions, offline
package restore and representative workload/operational/performance comparison
remain separate gates. No final ADR, foundation/OA implementation acceptance,
production package, release, deployment or migration is selected by this slice.


## Offline package restore on prepared Nix runtimes

The offline experiment prepares a public dependency archive from one committed
experiment revision. Preparation requires network access; the restore has a
separate filesystem root, PID namespace, and network namespace. The existing
read-only `/nix/store` supplies the explicitly recorded, locked native toolchain
and Chromium. This is a client package restore/build experiment on those runtime
prerequisites. It does not qualify a cold OS/Nix bootstrap, production database
restore, deployment recovery, or a package release.

Preparation refuses dirty or untracked experiment inputs. It archives the Git
revision and uses that extracted source for Python requirements, npm installation,
and Go/Rust vendoring. Python wheels retain the lockfile hashes; npm retains its
content-addressed cache and lockfile integrity; Cargo vendor checksums and Go's
locked module inputs are retained. The bundle contains Python 3.13.16 without
global site packages, the hash-recorded uv 0.13.0 binary, public CA certificates,
and FHS libraries resolved through the locked Nix loader/compiler runtime.
The manifest hashes every payload file and records exact native tool paths.
The preparation-only downloader is pip 26.2.1.

The caller supplies the archive SHA256 from the separate preparation receipt.
The bootstrap verifies that pin before executing the launcher contained in the
bundle. The launcher verifies the internal file set and hashes before restoring
anything into the new root. No host checkout, home, credential profile, ambient
PATH, package registry cache, virtualenv, node_modules, or compiler output is
mounted into that root. The child environment is cleared and populated explicitly;
its passwd fixture refers to a fresh directory. Loopback is enabled for the
real client/server checks, while an actual external IP connection must fail with
`ENETUNREACH`. That failure is paired with a working loopback echo control.

The runner deliberately removes a required Connect wheel and attempts an npm
install from an empty cache; both must fail. It then creates a fresh Python venv,
installs from the wheelhouse with hashes and no indexes, restores npm packages
with `--offline`, and cold-builds Go/Rust clients from vendored sources with
external registries disabled. TypeScript type checking, explicit read presence,
frozen legacy read behavior, native presence/CAS, generated Python Connect, and
the six actual Chromium/Connect/browser HTTP groups run from the restored source.
No prior native build output is supplied. Command durations are diagnostics,
not representative performance measurements.

The separate integrity checks change an archive byte, alter a manifest-bound
payload file, add an unlisted file, and modify a committed lockfile. Each must
refuse at its boundary. A positive control also demonstrates that the launcher
executed is the externally verified bundled copy. Run from a clean committed
checkout after entering the pinned Nix toolchain:

```bash
uv run --project tests/experiments/ec00/candidates --locked python -m unittest discover -s tests/experiments/ec00 -p offline_integrity_checks.py -v
uv run --project tests/experiments/ec00/candidates --locked python scripts/experiments/ec00/offline_packages.py prepare --destination /tmp/ec00-offline-bundle
# Read receipt.json, then supply its SHA256 explicitly:
uv run --project tests/experiments/ec00/candidates --locked python scripts/experiments/ec00/offline_packages.py run --archive /tmp/ec00-offline-bundle/offline-packages.tar --sha256 <receipt-sha256> --report /tmp/ec00-offline-result.json
```

The CI job enables unprivileged namespace support when the disposable Ubuntu
runner's AppArmor setting prevents it, then performs the same unprivileged
restore. It preserves receipts and diagnostics, including failures. It does
not upload the large dependency bundle or publish packages. Experimental ingress,
production authentication, streaming/backpressure, representative operations and
performance, and the final architectural comparison remain separate gates.

## Signed identity across candidate transports (19 October 2026)

The disposable adapters now use the unchanged service
`GatewayAssertionIdentityResolver` with an ephemeral Ed25519 fixture key. HTTP v2,
native gRPC, direct Python Connect JSON/binary and Node-forwarded Connect
JSON/binary carry the signed assertion and matching request correlation into a
request-local identity. The same SQLite owner resolves that verified principal
and rechecks active membership inside its transaction. Fixture read/write
authorities are explicit experiment policy, not a selected production OA model.

Before repair, both forwarding encodings dropped identity metadata; all five
browser paths also failed authenticated publication. The fixture clients,
forwarding edge and CORS proxy now preserve the assertion and correlation. They
do not turn an unsigned actor header into authority. The proxy preserves repeated
headers so an ambiguous request is refused rather than silently normalized.

The [source-bound receipt](authentication-2026-10-19/receipt.json) retains raw
before/after logs, including a development test-route error. Five tests pass:
four across all six native/generated transport combinations, plus actual
Chromium across five browser combinations. They cover signed publication,
assertion replay refusal, fresh-assertion retry of the same idempotency key,
changed-body refusal, wrong issuer/audience/workspace/repository/signature,
expired/future assertions, request correlation, missing/unsigned identity,
repeated identity/correlation headers, insufficient fixture authority, owner
role/membership refusal and revocation after commit. Sixteen concurrent
publications per arm (eight workers, two principals sharing key names) retain
principal-bound links and durable idempotency records. Every refusal checks the
owner rows for absence of effects. The six existing browser lifecycle/failure
checks, generated Python Connect check and TypeScript typecheck also pass.

This imports actual verifier source from the recorded Vuoro base, with candidate
runtime dependencies pinned in the candidate lock; it does not assert production
dependency parity. Keys and credentials are disposable, and no assertion is
included in receipts. These are bounded loopback examples. They do not qualify
production gateway/bootstrap, TLS, actual ingress, full authenticated lifecycle,
performance or an architecture choice. The EC-00 gate remains open.

## Review follow-ups and ingress fixture preparation (20 October 2026)

Three review advisories from the signed-identity slice are fixed:

- **Envelope binding.** The fixture owner now takes the verifier's invocation
  request id from the parsed body, as production does from the parsed envelope.
  It is read from the HTTP v2 JSON, Connect JSON or binary, and the native gRPC
  message. Before this, the fixture copied the `x-request-id` header. The
  candidate OA protocol has no separate invocation envelope, so the claim covers
  the message's own `request_id`. A new `envelope` case signs a claim that
  matches the header but not the body, and it is refused with no owner effect.
- **Identity context.** The test-thread `CURRENT.get()` check is gone. The
  fixture records, on the server side, the identity context each request sees
  at entry, at owner use and after reset. Over the real transports this shows
  that the runtimes isolate per-request contexts: grpcio runs each RPC in a
  fresh context, `ThreadingHTTPServer` uses a thread per request and uvicorn
  copies the context per task. Those entry/exit records therefore cannot fail
  on their own; what they do check is that the owner saw only the signing
  principal. (Correction to the first version of this note, which claimed reused
  gRPC threads proved no carry-over.) That `IdentityPolicy.bound()` resets its
  own binding is proven separately: a test drives it repeatedly, including a
  failing handler, inside one shared context on one reused thread, and fails
  when either reset is removed. The concurrency test is renamed to claim only
  the principal mapping.
- **Edge duplicates.** The Node Connect edge counts repeated
  `x-vuoro-identity`/`x-request-id` in `IncomingMessage.rawHeaders` and refuses
  them itself with Connect `unauthenticated` (401). Previously, `Headers.get`
  joined the values into a malformed token. The test asserts the edge's refusal
  marker and that no owner identity resolution took place.

[`tests/experiments/ec00/ingress/`](../../../tests/experiments/ec00/ingress/README.md)
now holds reviewable source for the isolated ingress workload:

- a locked image build;
- `serve_fixture.py`, which trusts only a mounted public key and serves a
  read-only `/_ec00/` readback;
- Kustomize resources for namespace `ec00-qualification`, attached to the
  Cilium Gateway `gateway/internal` listener `https`. HTTP and Connect get their
  own hostnames, and gRPC gets a separate hostname because of the Gateway API
  rule on HTTPRoute/GRPCRoute hostname conflicts;
- `remote_checks.py`, which generates a client-side key per run, verifies TLS
  against the system store, and records header, HTTP-version and grpc-web
  handling.

That README lists the operator build, push, apply, run and teardown commands.
The [local receipt](ingress-2026-10-20/receipt.json) covers only the built
image validated over plain loopback, with all 108 remote-mode cases and no
invariant failures. Its latency figures describe that local pair: the Connect
arm shows a fixed ~41 ms on the uvicorn/HTTP/1.1 path, against under 1 ms for
HTTP v2 and gRPC. That gap is a local artifact and not an ingress measurement.
TLS, the actual Gateway, Envoy header handling and ER-010 remain open until the
operator run. The EC-00 gate remains open.

### Actual Gateway run (20 October 2026)

The fixture image (`sha256:6029277f…7621`) ran once in a disposable
`ec00-qualification` namespace behind `gateway/internal`. All three routes
reported `Accepted` and `ResolvedRefs`. The workstation dialled 192.168.20.219
with verified TLS for each route hostname (gRPC and the raw HTTP/1.1 probe
against the host CA bundle, HTTP v2 and Connect through pyqwest's system-store
loader), and the namespace was deleted afterwards. The [gateway receipt](ingress-2026-10-20/gateway-receipt.json) and
[report](ingress-2026-10-20/gateway-report.json) record 108 cases: 100 security
assertions passed, 8 were observation-only, and there were no invariant failures.
Revocation after commit is not exercised remotely.

What the Gateway did (ER-010):

- It preserved `x-request-id` from these LAN clients. When the header was
  absent it added one, along with `x-envoy-internal`, which appeared only on
  that probe. A likely explanation is that Envoy treats LAN clients as internal
  and does not regenerate the header; that was not observed directly. Clients
  outside the internal range have not been tested.
- Repeated `x-vuoro-identity` arrived as two headers, while repeated
  `x-request-id` arrived joined into one comma-separated value. Both were
  refused with no owner effect on every arm.
- HTTP and Connect reached the backend as HTTP/1.1 even over client HTTP/2;
  only the h2c gRPC route stayed HTTP/2.
- It translated grpc-web on the gRPC hostname into native gRPC, and a signed
  grpc-web publication took effect.
- It forwarded header names containing underscores unchanged. The production
  verifier (FastAPI/ASGI) matches exact names and no underscore identity header
  was sent, so this is not an observed bypass. Rejecting or dropping underscore
  header names at the ingress or backend is a candidate defence-in-depth
  requirement, since a backend that maps `_` to `-` could see a second identity
  or correlation header.

Latency over n=20 per case: HTTP v2 and gRPC signed publication p50 about
9–11 ms (p95 about 14.5 ms); Connect about 56 ms. Connect has a ~41 ms floor
that also appears over loopback; over the Gateway, signed Connect adds about
14 ms against about 8–10 ms for HTTP v2 and gRPC. Only the floor is not a
Gateway cost. These are single-run
fixture figures, not a performance comparison. A second deployment of the reviewed fixture (`sha256:0a926fb6…1ede`, run 3 in the receipt,
[report](ingress-2026-10-20/gateway-report-r2.json)) reproduced the header, version and
grpc-web observations; its latency figures differ (see the receipt):
108 cases, no security or expectation failures, no ER-010 collapse warnings,
repeated identity headers kept separate and repeated `x-request-id` joined, all
refused without effect. The EC-00 gate remains open.

## Native cancellation and durable cursor observations (23 October 2026)

The existing generated HTTP v2 runners now have experiment-only `attempt`,
`reconcile` and `watch` modes. TypeScript uses native `AbortSignal`, Go uses
`context` cancellation/deadlines, and Rust drops dispatched reqwest futures on
in-flight cancellation or Tokio timeout. Rust's pre-dispatch cancellation drops
an unpolled generated future: this is a language-level no-dispatch control,
not evidence that generated SDK transport machinery processed cancellation.
That SDK cancellation qualification remains missing. Each new client process
reads the logical key and cursor from the same coordinator-written restart file. The Python control
also pauses a real generated httpx request before socket send: cancellation and
an expired asyncio await budget dispatch no owner call. This await budget is
separate from the existing httpx socket read-timeout control.

The command histories distinguish three boundaries. Pre-dispatch controls cause
no owner call or effect; TS/Go exercise generated calls with native cancellation
and deadline options, while Rust's unpolled cancellation has the narrower scope
above. A dispatched handler paused before invocation can still commit after its
client stops waiting; a
handler paused after its committed return has already committed. Same-key
reconciliation by fresh native processes and a restarted fixture server returns
revision `2` once, while changed meaning receives 409. Complete independent
SQLite request/event/idempotency rows prove one transition. Cancellation of a
client wait does not establish owner rollback.

For each native language, six watch histories lose, cancel or time out the
response before or after the real owner read. Replaying the same persisted
cursor returns the identical ordered page. New cursor pages cover the complete
persisted event sequence without gaps, including across client-process and
fixture-server restart. Cross-principal/workspace and expired reads receive
explicit 409 refusals with unchanged owner rows. Revocation uses a separate
otherwise valid terminal cursor at position 5: after compaction moves the
retention floor to 6, each native client first reads that cursor successfully
(200), then receives 409 after actual identity revocation. The old position-2
cursor separately proves expiry. Disabling revocation must make the valid-cursor
guard fail. These are fresh client processes and fresh in-process fixture
server instances; they are not a
production server crash/recovery qualification.

Actual unread response consumers exposed a fixture resource gap. Two-event
pages were bounded, but completed `ThreadingHTTPServer` handlers left growing
kernel FIN state for stalled peers. A first bounded repair still left an orphan
TIME_WAIT socket after a valid progress control, which the kernel-state guard
rejected. The fixture now admits at most 16 connections before dispatch, has a
listen backlog of 16, uses 16 fixed HTTP worker threads and at most 16 connection
deadline timers, and retains a slot through response drain. An absolute two
second socket lifetime aborts stalled connections. Responses state
`Connection: close` and retain their existing Content-Length and HTTP/1.0
one-request behavior. Closing the socket does not cancel an owner operation;
its slot remains held until the handler and its timer finish.

The slow-consumer check leaves actual response bodies unread in three cohorts
of 40 loopback connections. Each cohort observes 16 admitted connections,
24 refusals before owner dispatch, at most 32 owned worker/timer threads, a
358-byte two-event response, at most 16 server-side kernel connections, and no
remaining non-listener kernel states after settlement. Valid cursor reads
continue after each cohort. Enlarging capacity to 64 and extending the deadline
to ten seconds both make this guard fail. This is a bounded unary cursor fixture
on the measured Linux loopback path, not a streaming, SSE, arbitrary-rate or
production ingress backpressure qualification.

The native-wake case remains **missing**. The actual Sprintctl capability
contract declares `read-while-running`, `attention-during-run` and
`start-after-stop`; it does not implement an unavailable-wake/continuation-ready
boundary. Its EC05 source inventory explicitly leaves native runtime adapters
to EC09 and does not mark the fallback delivered. The disposable OA fixture's
continuation acknowledgement is not native wake, and no fake wake route or
passing placeholder was added. ER-002 and EC00 remain open, including the
production generation, ingress, workload and operational conditions.

Run the existing `transport_failure_checks.py`, `watch_replay_checks.py` and
`versioned_native_checks.py` commands in the pinned Nix environment above.
Missing pinned runtimes fail instead of skipping. Historical IDL, generated
clients, locks, OwnerOracle and the unknown-value fixtures remain unchanged.

### Rust SDK pre-dispatch boundary (31 October 2026)

`rust_predispatch_checks.py` polls a generated v2 request into reqwest's custom
DNS resolver before dropping the call or expiring its await budget, then releases
the resolver and compares independent persisted owner rows. The explicit check
passed with the exact locked Nix Cargo, Rust, GCC wrapper and protoc outputs;
Cargo reports 1.98.0 and Rust 1.98.1. An admitted generated call changed owner
rows and failed the no-effect assertion when applied to it; an unpolled future
failed the transport-entry assertion. The Python pre-dispatch regression also
passed. CI now invokes the Rust check explicitly. No generated schema or owner
changes are involved.

The complete locked shell still failed offline on a fixed-output TinyCC source
hash mismatch; the individual locked build outputs were used without changing
the pin or hash. The broader multi-language transport regression was interrupted
after TypeScript cases failed because this checkout lacked its npm dependencies;
the pinned Go output was also absent. The Rust observation is limited to this
loopback barrier and does not establish owner rollback, native wake, production
ingress behavior, or an architecture decision. Owner acceptance and the other
EC-00 gates remain open.
