import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { createClient } from "../generated/typespec/clients/typescript/client";
import { publish, readArtifact, reportPerformed, revise, withdrawDependent } from "../generated/typespec/clients/typescript/sdk.gen";
import {
  MutationResultSchema,
  ReadArtifactRequestSchema,
  JsonResultSchema,
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
    const read = await readArtifact({ client, path: { requestId: id, materialRevision: 1 } });
    if (read.error || !read.data?.json) throw new Error("TypeSpec signed-artifact read failed");
    const stored = JSON.parse(read.data.json);
    if (stored.artifactDigest !== signed.artifact_digest || stored.originalBytes !== signed.original_bytes ||
        stored.envelopeBytes !== signed.envelope_bytes || stored.signature !== signed.signature) {
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
    const wire = fromBinary(JsonResultSchema, new Uint8Array(await read.arrayBuffer()));
    const stored = JSON.parse(wire.json);
    if (stored.artifactDigest !== signed.artifact_digest || stored.originalBytes !== signed.original_bytes ||
        stored.envelopeBytes !== signed.envelope_bytes || stored.signature !== signed.signature) {
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
  console.log(JSON.stringify({ httpLifecycleId, protoLifecycleId }));
}
