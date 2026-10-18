// Runtime check using the frozen generated TypeScript clients from 5ff5d823.
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { createClient } from "./generated/typespec/clients/typescript/client";
import { publish, revise } from "./generated/typespec/clients/typescript/sdk.gen";
import { MutationResultSchema, PublishRequestSchema, ReviseRequestSchema } from "./generated/protobuf/typescript/oa.v1_pb";

declare const process: { argv: string[] };
const baseUrl = process.argv[2];
if (!baseUrl) throw new Error("base URL required");

async function protoRequest(path: string, method: string, message: Uint8Array, key: string) {
  return fetch(baseUrl + path, { method, body: message as unknown as BodyInit,
    headers: { "Content-Type": "application/protobuf", "Idempotency-Key": key } });
}

const protoId = "9007199254741211";
const protoPublished = await protoRequest("/proto/v1/requests", "POST",
  toBinary(PublishRequestSchema, create(PublishRequestSchema, { requestId: protoId, objective: "frozen protobuf TypeScript publish" })),
  "old-ts-proto-publish");
if (protoPublished.status !== 200) throw new Error(`old protobuf publish returned ${protoPublished.status}`);
const protoResult = fromBinary(MutationResultSchema, new Uint8Array(await protoPublished.arrayBuffer()));
if (protoResult.revision !== 1n) throw new Error("old protobuf publish revision mismatch");
const protoRevised = await protoRequest(`/proto/v1/requests/${protoId}`, "PATCH",
  toBinary(ReviseRequestSchema, create(ReviseRequestSchema, {
    requestId: protoId, expectedRevision: 1n, objective: "frozen protobuf TypeScript revise",
  })), "old-ts-proto-revise");
if (protoRevised.status !== 200 || fromBinary(MutationResultSchema,
    new Uint8Array(await protoRevised.arrayBuffer())).revision !== 2n) throw new Error("old protobuf revise failed");

const httpId = "9007199254741213";
const httpPublished = await publish({ client: createClient({ baseUrl,
  headers: { "Idempotency-Key": "old-ts-openapi-publish" } }),
  body: { requestId: httpId, objective: "frozen OpenAPI TypeScript publish" } });
if (httpPublished.error || httpPublished.data?.revision !== 1) throw new Error("old OpenAPI publish failed");
const httpRevised = await revise({ client: createClient({ baseUrl,
  headers: { "Idempotency-Key": "old-ts-openapi-revise" } }),
  path: { requestId: httpId }, body: { requestId: httpId, expectedRevision: 1,
    objective: "frozen OpenAPI TypeScript revise" } });
if (httpRevised.error || httpRevised.data?.revision !== 2) throw new Error("old OpenAPI revise failed");
console.log(JSON.stringify({ protoId, httpId, revisions: [2, 2] }));
