import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { createClient } from "../generated/typespec-v2/clients/typescript/client";
import {
  listRequests,
  watch,
} from "../generated/typespec-v2/clients/typescript/sdk.gen";
import {
  PageRequestV2Schema,
  RequestPageSchema,
  WatchPageSchema,
} from "../generated/protobuf/typescript/oa.v1_pb";

declare const process: { argv: string[] };

const baseUrl = process.argv[2];
if (!baseUrl) throw new Error("base URL positional argument required");

const cases = [
  { name: "omitted", fields: {} },
  { name: "zero", fields: { limit: 0 } },
  { name: "empty", fields: { cursor: "" } },
  { name: "one", fields: { limit: 1 } },
  { name: "negative", fields: { limit: -1 } },
  { name: "oversized", fields: { limit: 3 } },
] as const;

const hex = (bytes: Uint8Array) =>
  Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");

const rows: Array<Record<string, unknown>> = [];
for (const route of ["list-requests", "watch"] as const) {
  for (const testCase of cases) {
    const request = create(PageRequestV2Schema, testCase.fields);
    const requestBytes = toBinary(PageRequestV2Schema, request);
    const decoded = fromBinary(PageRequestV2Schema, requestBytes);
    const query: { cursor?: string; limit?: number } = {};
    if ("cursor" in testCase.fields) query.cursor = testCase.fields.cursor;
    if ("limit" in testCase.fields) query.limit = testCase.fields.limit;

    const protoResponse = await fetch(`${baseUrl}/proto/v2/${route}`, {
      method: "POST",
      headers: { "Content-Type": "application/protobuf" },
      body: requestBytes as unknown as BodyInit,
    });
    let protoHex: string | null = null;
    if (protoResponse.status === 200) {
      const responseBytes = new Uint8Array(await protoResponse.arrayBuffer());
      if (route === "list-requests") fromBinary(RequestPageSchema, responseBytes);
      else fromBinary(WatchPageSchema, responseBytes);
      protoHex = hex(responseBytes);
    }

    const client = createClient({ baseUrl });
    const httpResult = route === "list-requests"
      ? await listRequests({ client, query })
      : await watch({ client, query });
    const httpBody = httpResult.data ?? httpResult.error ?? null;

    rows.push({
      route,
      case: testCase.name,
      protoStatus: protoResponse.status,
      httpStatus: httpResult.response.status,
      requestHex: hex(requestBytes),
      decodedCursorPresent: decoded.cursor !== undefined,
      decodedLimitPresent: decoded.limit !== undefined,
      protoHex,
      httpBody,
    });
  }
}

console.log(JSON.stringify(rows));
