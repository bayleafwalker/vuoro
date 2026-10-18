import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { createClient } from "../generated/typespec/clients/typescript/client";
import { publish, revise } from "../generated/typespec/clients/typescript/sdk.gen";
import {
  MutationResultSchema,
  PublishRequestSchema,
  ReviseRequestSchema,
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
    note: "",
  })),
  `proto-revise-one-${protoId}`,
  "PATCH",
);
await assertStatus(protoRevised, 200, "protobuf valid revise");
const protoRevisedResult = fromBinary(MutationResultSchema, new Uint8Array(await protoRevised.arrayBuffer()));
if (protoRevisedResult.revision !== 2n) throw new Error("protobuf revise result mismatch");

const httpId = "9007199254740995";
const httpAbsentNoteId = "9007199254740997";
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

console.log(JSON.stringify({ protoId, protoRevision: Number(protoRevisedResult.revision), httpId, httpAbsentNoteId,
  httpRevision: httpGood.data.revision, protoAbsentStatus: protoAbsent.status,
  protoZeroStatus: protoZero.status, httpAbsentStatus: 422, httpNullStatus: 422, httpZeroStatus: 409 }));
