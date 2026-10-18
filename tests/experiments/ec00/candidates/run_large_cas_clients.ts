import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { createClient } from "../generated/typespec/clients/typescript/client";
import { revise } from "../generated/typespec/clients/typescript/sdk.gen";
import { MutationResultSchema, ReviseRequestSchema } from "../generated/protobuf/typescript/oa.v1_pb";

declare const process: { env: Record<string, string | undefined> };
const baseUrl = process.env.EC00_BASE_URL!;
const expected = process.env.EC00_EXPECTED_REVISION!;
const requestId = "9007199254741199";
if (!baseUrl || !expected) throw new Error("fixture URL and revision required");
if (process.env.EC00_CAS_ARM === "openapi") {
  let wireRequest = "", wireResponse = "";
  const client = createClient({ baseUrl, headers: { "Idempotency-Key": "large-cas" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      wireRequest = await request.clone().text();
      const response = await fetch(request);
      wireResponse = await response.clone().text();
      return response;
    },
  });
  const result = await revise({ client, path: { requestId }, body: {
    requestId, expectedRevision: Number(expected), objective: "large CAS effect",
  } });
  console.log(JSON.stringify({ status: result.response.status, wireRequest, wireResponse,
    clientRevision: result.data ? String(result.data.revision) : null }));
} else if (process.env.EC00_CAS_ARM === "protobuf") {
  const bytes = toBinary(ReviseRequestSchema, create(ReviseRequestSchema, {
    requestId, expectedRevision: BigInt(expected), objective: "large CAS effect",
  }));
  const sent = fromBinary(ReviseRequestSchema, bytes).expectedRevision;
  const response = await fetch(`${baseUrl}/proto/v1/requests/${requestId}`, {
    method: "PATCH", headers: { "Content-Type": "application/protobuf", "Idempotency-Key": "large-cas" },
    body: bytes as unknown as BodyInit,
  });
  if (response.status !== 200) throw new Error(`protobuf CAS refused: ${response.status}`);
  const result = fromBinary(MutationResultSchema, new Uint8Array(await response.arrayBuffer()));
  console.log(JSON.stringify({ status: response.status, sentRevision: String(sent),
    clientRevision: String(result.revision), requestId: result.requestId }));
} else throw new Error("unknown arm");
