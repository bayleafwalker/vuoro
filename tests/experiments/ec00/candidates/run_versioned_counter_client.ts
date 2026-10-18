import { createClient } from "../generated/typespec-v2/clients/typescript/client";
import { revise } from "../generated/typespec-v2/clients/typescript/sdk.gen";

declare const process: { env: Record<string, string | undefined> };
const baseUrl = process.env.EC00_BASE_URL!;
const expected = process.env.EC00_EXPECTED_REVISION!;
if (!baseUrl || !expected) throw new Error("fixture URL and revision required");
const wire: Array<{request: string; response: string}> = [];
const client = createClient({ baseUrl, headers: { "Idempotency-Key": "v2-cas" },
  fetch: async (input, init) => {
    const request = new Request(input, init);
    const body = await request.clone().text();
    const response = await fetch(request);
    wire.push({request: body, response: await response.clone().text()});
    return response;
  },
});
const requestId = "9007199254741199";
const body = { requestId, expectedRevision: expected, objective: "versioned counter effect" };
const first = await revise({client, path: {requestId}, body});
const replay = await revise({client, path: {requestId}, body});
const changed = await revise({client, path: {requestId}, body: {...body, objective: "changed versioned effect"}});
if (first.response.status !== 200 || replay.response.status !== 200 || changed.response.status !== 409)
  throw new Error(`unexpected mutation/replay/refusal ${first.response.status}/${replay.response.status}/${changed.response.status}`);
if (first.data?.requestId !== requestId || replay.data?.requestId !== requestId)
  throw new Error("v2 first/replay request identity mismatch");
if (typeof first.data?.revision !== "string" || typeof replay.data?.revision !== "string")
  throw new Error("v2 mutation result must preserve decimal text");
console.log(JSON.stringify({language: "typescript", requestId: first.data.requestId,
  revision: first.data.revision, replayRevision: replay.data.revision,
  changedStatus: changed.response.status, wire}));
