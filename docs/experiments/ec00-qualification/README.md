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
