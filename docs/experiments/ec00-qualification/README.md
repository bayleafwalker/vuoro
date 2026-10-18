# EC-00 disposable qualification workspace

This directory is an experiment input, not a Vuoro production contract, schema,
API, selected architecture, or owner implementation. It exists to make the
architecture comparison reproducible after EC-01 provides observed consumers
and payload classes. Do not publish these Python objects as an API.

## Source and toolchain observations

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

## Offline check

Run with standard library only:

```bash
python -m unittest discover -s tests/experiments/ec00 -p 'test_*.py' -v
```
