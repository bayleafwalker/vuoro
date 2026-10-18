import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { createClient } from "../generated/typespec/clients/typescript/client";
import { listRequests, publish, readArtifact, readDependants, readRequest, reportPerformed, revise, watch, withdrawDependent } from "../generated/typespec/clients/typescript/sdk.gen";
import {
  MutationResultSchema,
  ArtifactReadSchema,
  RequestReadSchema,
  DependantsReadSchema,
  RequestPageSchema,
  WatchPageSchema,
  PublishRequestSchema,
  ReportPerformedRequestSchema,
  ReviseRequestSchema,
  WithdrawDependentRequestSchema,
} from "../generated/protobuf/typescript/oa.v1_pb";

declare const process: { env: Record<string, string | undefined> };

const baseUrl = process.env.EC00_BASE_URL;
if (!baseUrl) throw new Error("EC00_BASE_URL required");

async function protoRequest(path: string, bytes: Uint8Array, idem: string, method = "POST") {
  return fetch(`${baseUrl}${path}`, {
    method,
    headers: { "Content-Type": "application/protobuf", "Idempotency-Key": idem },
    body: bytes as unknown as BodyInit,
  });
}

async function assertStatus(response: Response, expected: number, label: string) {
  if (response.status !== expected) {
    throw new Error(`${label}: expected ${expected}, got ${response.status}`);
  }
}

const protoId = "9007199254740993";
const missingObjective = await protoRequest(
  "/proto/v1/requests",
  toBinary(PublishRequestSchema, create(PublishRequestSchema, { requestId: "9007199254740992" })),
  "proto-publish-missing-objective",
);
await assertStatus(missingObjective, 422, "protobuf missing objective");
const emptyObjective = await protoRequest(
  "/proto/v1/requests",
  toBinary(PublishRequestSchema, create(PublishRequestSchema, { requestId: "9007199254740994", objective: "" })),
  "proto-publish-empty-objective",
);
await assertStatus(emptyObjective, 200, "protobuf present empty objective");
const protoNullNoteId = "9007199254740998";
const protoNullNote = await protoRequest(
  "/proto/v1/requests",
  toBinary(PublishRequestSchema, create(PublishRequestSchema, {
    requestId: protoNullNoteId,
    objective: "protobuf TypeScript null note",
    noteValue: { case: "noteNull", value: 0 },
  })),
  `proto-publish-null-note-${protoNullNoteId}`,
);
await assertStatus(protoNullNote, 200, "protobuf explicit null note");
const protoPublished = await protoRequest(
  "/proto/v1/requests",
  toBinary(PublishRequestSchema, create(PublishRequestSchema, {
    requestId: protoId,
    objective: "protobuf TypeScript publish",
  })),
  `proto-publish-${protoId}`,
);
await assertStatus(protoPublished, 200, "protobuf publish");
const protoResult = fromBinary(MutationResultSchema, new Uint8Array(await protoPublished.arrayBuffer()));
if (protoResult.requestId !== protoId || protoResult.revision !== 1n) {
  throw new Error("protobuf publish result mismatch");
}

const protoAbsent = await protoRequest(
  `/proto/v1/requests/${protoId}`,
  toBinary(ReviseRequestSchema, create(ReviseRequestSchema, {
    requestId: protoId,
    objective: "absent expected revision",
  })),
  `proto-revise-absent-${protoId}`,
  "PATCH",
);
await assertStatus(protoAbsent, 422, "protobuf absent expectedRevision");
const protoZero = await protoRequest(
  `/proto/v1/requests/${protoId}`,
  toBinary(ReviseRequestSchema, create(ReviseRequestSchema, {
    requestId: protoId,
    expectedRevision: 0n,
    objective: "zero expected revision",
  })),
  `proto-revise-zero-${protoId}`,
  "PATCH",
);
await assertStatus(protoZero, 409, "protobuf zero expectedRevision");
const protoRevised = await protoRequest(
  `/proto/v1/requests/${protoId}`,
  toBinary(ReviseRequestSchema, create(ReviseRequestSchema, {
    requestId: protoId,
    expectedRevision: 1n,
    objective: "protobuf TypeScript revision",
    noteValue: { case: "note", value: "" },
  })),
  `proto-revise-one-${protoId}`,
  "PATCH",
);
await assertStatus(protoRevised, 200, "protobuf valid revise");
const protoRevisedResult = fromBinary(MutationResultSchema, new Uint8Array(await protoRevised.arrayBuffer()));
if (protoRevisedResult.revision !== 2n) throw new Error("protobuf revise result mismatch");

const httpId = "9007199254740995";
const httpAbsentNoteId = "9007199254740997";
const httpEmptyNoteId = "9007199254740999";
const httpPub = await publish({
  client: createClient({ baseUrl, headers: { "Idempotency-Key": `http-publish-${httpId}` } }),
  body: { requestId: httpId, objective: "TypeSpec TypeScript publish", note: null },
});
if (httpPub.error || httpPub.data?.requestId !== httpId || httpPub.data.revision !== 1) {
  throw new Error(`OpenAPI TypeScript publish mismatch: ${JSON.stringify(httpPub)}`);
}
const httpAbsentNote = await publish({
  client: createClient({ baseUrl, headers: { "Idempotency-Key": `http-publish-note-unset-${httpAbsentNoteId}` } }),
  body: { requestId: httpAbsentNoteId, objective: "TypeSpec TypeScript absent note" },
});
if (httpAbsentNote.error || httpAbsentNote.data?.requestId !== httpAbsentNoteId) {
  throw new Error(`OpenAPI TypeScript absent-note publish mismatch: ${JSON.stringify(httpAbsentNote)}`);
}
const httpEmptyNote = await publish({
  client: createClient({ baseUrl, headers: { "Idempotency-Key": `http-publish-note-empty-${httpEmptyNoteId}` } }),
  body: { requestId: httpEmptyNoteId, objective: "TypeSpec TypeScript empty note", note: "" },
});
if (httpEmptyNote.error || httpEmptyNote.data?.requestId !== httpEmptyNoteId) {
  throw new Error(`OpenAPI TypeScript empty-note publish mismatch: ${JSON.stringify(httpEmptyNote)}`);
}

async function httpRevise(expectedRevision: number | null | undefined, key: string) {
  const body: Record<string, unknown> = { requestId: httpId, objective: "TypeSpec TypeScript revise" };
  if (expectedRevision !== undefined) body.expectedRevision = expectedRevision;
  const result = await revise({
    client: createClient({ baseUrl, headers: { "Idempotency-Key": key } }),
    path: { requestId: httpId },
    body: body as never,
  });
  return result.response.status;
}

if (await httpRevise(undefined, `http-revise-absent-${httpId}`) !== 422) {
  throw new Error("OpenAPI absent expectedRevision was not refused");
}
if (await httpRevise(null, `http-revise-null-${httpId}`) !== 422) {
  throw new Error("OpenAPI null expectedRevision was not refused");
}
if (await httpRevise(0, `http-revise-zero-${httpId}`) !== 409) {
  throw new Error("OpenAPI zero expectedRevision did not reach owner stale-CAS refusal");
}
const httpGood = await revise({
  client: createClient({ baseUrl, headers: { "Idempotency-Key": `http-revise-one-${httpId}` } }),
  path: { requestId: httpId },
  body: { requestId: httpId, expectedRevision: 1, objective: "TypeSpec TypeScript revision", note: null },
});
if (httpGood.error || httpGood.data?.revision !== 2) {
  throw new Error(`OpenAPI TypeScript revise mismatch: ${JSON.stringify(httpGood)}`);
}

console.log(JSON.stringify({ protoId, protoRevision: Number(protoRevisedResult.revision), protoNullNoteId,
  httpId, httpAbsentNoteId, httpEmptyNoteId,
  httpRevision: httpGood.data.revision, protoAbsentStatus: protoAbsent.status,
  protoZeroStatus: protoZero.status, httpAbsentStatus: 422, httpNullStatus: 422, httpZeroStatus: 409 }));

// The lifecycle runner supplies an Ed25519-signed disposable artifact bundle.
// This extends both generated client arms against the same owner database; the
// Python harness binds the coordinator identity out of band for this process.
const artifactWire = process.env.EC00_SIGNED_ARTIFACT;
if (artifactWire) {
  const artifacts = JSON.parse(artifactWire) as Record<string, {
    artifact_digest: string; key_id: string; original_bytes: string;
    envelope_bytes: string; signature: string;
  }>;
  const decode = (value: string) => Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
  const encode = (value: Uint8Array) => btoa(String.fromCharCode(...value));
  const runHttpLifecycle = async () => {
    const id = "9007199254741123";
    const signed = artifacts.http;
    const client = createClient({ baseUrl });
    const pub = await publish({ client, headers: { "Idempotency-Key": `ts-life-pub-${id}` }, body: {
      requestId: id, objective: "generated TypeSpec lifecycle", dependants: ["work-a"],
      artifactDigest: signed.artifact_digest, artifactKeyId: signed.key_id,
      artifactBytes: signed.original_bytes, artifactEnvelope: signed.envelope_bytes,
      artifactSignature: signed.signature,
    } as never });
    if (pub.error || pub.data?.revision !== 1) throw new Error(`TypeSpec lifecycle publish failed: ${JSON.stringify(pub)}`);
    const read = await readArtifact({ client, path: { requestId: id, materialRevision: "1" } });
    if (read.error || !read.data) throw new Error("TypeSpec signed-artifact read failed");
    if (read.data.artifactDigest !== signed.artifact_digest || read.data.originalBytes !== signed.original_bytes ||
        read.data.envelopeBytes !== signed.envelope_bytes || read.data.signature !== signed.signature) {
      throw new Error("TypeSpec signed-artifact read did not preserve exact bytes");
    }
    const withdrawn = await withdrawDependent({ client, path: { requestId: id },
      headers: { "Idempotency-Key": `ts-life-withdraw-${id}` },
      body: { expectedRevision: 1, dependentId: "work-a" } });
    if (withdrawn.error || withdrawn.data?.revision !== 2) throw new Error("TypeSpec dependent withdrawal failed");
    const reported = await reportPerformed({ client, path: { requestId: id },
      headers: { "Idempotency-Key": `ts-life-report-${id}` },
      body: { expectedRevision: 2 } });
    if (reported.error || reported.data?.revision !== 3) throw new Error("TypeSpec report failed");
    return id;
  };
  const runProtoLifecycle = async () => {
    const id = "9007199254741125";
    const signed = artifacts.proto;
    const pub = await protoRequest("/proto/v1/requests",
      toBinary(PublishRequestSchema, create(PublishRequestSchema, {
        requestId: id, objective: "generated protobuf lifecycle", dependentIds: ["work-a"],
        artifactDigest: signed.artifact_digest, artifactKeyId: signed.key_id,
        artifactBytes: decode(signed.original_bytes), artifactEnvelope: decode(signed.envelope_bytes),
        artifactSignature: decode(signed.signature),
      })), `ts-proto-life-pub-${id}`);
    await assertStatus(pub, 200, "protobuf lifecycle publish");
    const read = await fetch(`${baseUrl}/proto/v1/requests/${id}/artifact/1`);
    await assertStatus(read, 200, "protobuf lifecycle artifact read");
    const stored = fromBinary(ArtifactReadSchema, new Uint8Array(await read.arrayBuffer()));
    if (stored.artifactDigest !== signed.artifact_digest || encode(stored.originalBytes) !== signed.original_bytes ||
        encode(stored.envelopeBytes) !== signed.envelope_bytes || encode(stored.signature) !== signed.signature) {
      throw new Error("protobuf signed-artifact read did not preserve exact bytes");
    }
    const withdrawn = await protoRequest(`/proto/v1/requests/${id}/withdraw`,
      toBinary(WithdrawDependentRequestSchema, create(WithdrawDependentRequestSchema, {
        requestId: id, expectedRevision: 1n, dependentId: "work-a",
      })), `ts-proto-life-withdraw-${id}`);
    await assertStatus(withdrawn, 200, "protobuf dependent withdrawal");
    const reported = await protoRequest(`/proto/v1/requests/${id}/report`,
      toBinary(ReportPerformedRequestSchema, create(ReportPerformedRequestSchema, {
        requestId: id, expectedRevision: 2n,
      })), `ts-proto-life-report-${id}`);
    await assertStatus(reported, 200, "protobuf report performed");
    return id;
  };
  const [httpLifecycleId, protoLifecycleId] = await Promise.all([runHttpLifecycle(), runProtoLifecycle()]);
  const bigReadId = process.env.EC00_BIG_READ_ID;
  if (bigReadId) {
    const bigSigned = JSON.parse(process.env.EC00_BIG_READ_ARTIFACT ?? "{}") as {
      artifact_digest: string; key_id: string; original_bytes: string;
      envelope_bytes: string; signature: string;
    };
    const largeRevision = "9007199254740995";
    const largeMaterialRevision = "9007199254740993";
    const largeSequence = "9007199254740997";
    const client = createClient({ baseUrl });
    const request = await readRequest({ client, path: { requestId: bigReadId } });
    if (request.error || request.data?.revision !== largeRevision ||
        request.data.materialRevision !== largeMaterialRevision || request.data.createdSeq !== largeSequence ||
        request.data.state !== "open" || request.data.body.objective !== "typed int64 read fixture") {
      throw new Error(`TypeSpec typed large-int64 request read mismatch: ${JSON.stringify(request)}`);
    }
    const deps = await readDependants({ client, path: { requestId: bigReadId } });
    if (deps.error || deps.data?.items.length !== 1 || deps.data.items[0].dependentId !== "work-a") {
      throw new Error("TypeSpec typed dependant read mismatch");
    }
    const artifact = await readArtifact({ client, path: { requestId: bigReadId, materialRevision: largeMaterialRevision } });
    if (artifact.error || artifact.data?.materialRevision !== largeMaterialRevision ||
        artifact.data.originalBytes !== "" || artifact.data.artifactDigest !== bigSigned.artifact_digest ||
        artifact.data.envelopeBytes !== bigSigned.envelope_bytes || artifact.data.signature !== bigSigned.signature) {
      throw new Error("TypeSpec large material revision or empty artifact bytes were not preserved");
    }
    let cursor: string | undefined;
    let foundSummary = false;
    do {
      const page = await listRequests({ client, query: { cursor, limit: 2 } });
      if (page.error || !page.data) throw new Error("TypeSpec typed request page read failed");
      foundSummary ||= page.data.items.some((item) => item.requestId === bigReadId &&
        item.revision === largeRevision && item.createdSeq === largeSequence);
      cursor = page.data.nextCursor ?? undefined;
    } while (cursor && !foundSummary);
    if (!foundSummary) throw new Error("TypeSpec typed page lost a large int64 summary value");
    cursor = undefined;
    let foundEvent = false;
    do {
      const page = await watch({ client, query: { cursor, limit: 2 } });
      if (page.error || !page.data) throw new Error("TypeSpec typed watch page read failed");
      foundEvent ||= page.data.events.some((event) => event.requestId === bigReadId &&
        event.revision === largeRevision && event.seq === largeSequence);
      cursor = page.data.nextCursor ?? undefined;
    } while (cursor && !foundEvent);
    if (!foundEvent) throw new Error("TypeSpec typed watch lost a large int64 event value");

    const protoRequest = await fetch(`${baseUrl}/proto/v1/requests/${bigReadId}`);
    const protoRequestValue = fromBinary(RequestReadSchema, new Uint8Array(await protoRequest.arrayBuffer()));
    if (protoRequestValue.revision !== BigInt(largeRevision) ||
        protoRequestValue.materialRevision !== BigInt(largeMaterialRevision) ||
        protoRequestValue.createdSeq !== BigInt(largeSequence) || protoRequestValue.state !== 1) {
      throw new Error("protobuf TypeScript typed large-int64 request read mismatch");
    }
    const protoDepsResponse = await fetch(`${baseUrl}/proto/v1/requests/${bigReadId}/dependants`);
    const protoDeps = fromBinary(DependantsReadSchema, new Uint8Array(await protoDepsResponse.arrayBuffer()));
    if (protoDeps.items.length !== 1 || protoDeps.items[0].dependentId !== "work-a") {
      throw new Error("protobuf TypeScript typed dependant read mismatch");
    }
    const protoArtifactResponse = await fetch(`${baseUrl}/proto/v1/requests/${bigReadId}/artifact/${largeMaterialRevision}`);
    const protoArtifact = fromBinary(ArtifactReadSchema, new Uint8Array(await protoArtifactResponse.arrayBuffer()));
    if (protoArtifact.materialRevision !== BigInt(largeMaterialRevision) || protoArtifact.originalBytes.length !== 0 ||
        protoArtifact.artifactDigest !== bigSigned.artifact_digest ||
        encode(protoArtifact.envelopeBytes) !== bigSigned.envelope_bytes ||
        encode(protoArtifact.signature) !== bigSigned.signature) {
      throw new Error("protobuf large material revision or empty bytes mismatch");
    }
    let protoCursor: string | undefined;
    let protoSummaryFound = false;
    do {
      const query = new URLSearchParams({ limit: "2" });
      if (protoCursor) query.set("cursor", protoCursor);
      const response = await fetch(`${baseUrl}/proto/v1/requests?${query}`);
      const page = fromBinary(RequestPageSchema, new Uint8Array(await response.arrayBuffer()));
      protoSummaryFound ||= page.items.some((item) => item.requestId === bigReadId &&
        item.revision === BigInt(largeRevision) && item.createdSeq === BigInt(largeSequence));
      protoCursor = page.nextCursor || undefined;
    } while (protoCursor && !protoSummaryFound);
    if (!protoSummaryFound) throw new Error("protobuf typed request page lost large int64 summary values");
    protoCursor = undefined;
    let protoEventFound = false;
    do {
      const query = new URLSearchParams({ limit: "2" });
      if (protoCursor) query.set("cursor", protoCursor);
      const response = await fetch(`${baseUrl}/proto/v1/events?${query}`);
      const page = fromBinary(WatchPageSchema, new Uint8Array(await response.arrayBuffer()));
      protoEventFound ||= page.events.some((event) => event.requestId === bigReadId &&
        event.revision === BigInt(largeRevision) && event.seq === BigInt(largeSequence));
      protoCursor = page.nextCursor || undefined;
    } while (protoCursor && !protoEventFound);
    if (!protoEventFound) throw new Error("protobuf typed watch lost large int64 event values");
  }
  console.log(JSON.stringify({ httpLifecycleId, protoLifecycleId }));
}
