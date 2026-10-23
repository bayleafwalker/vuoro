import { createClient } from "../generated/typespec-v2/clients/typescript/client";
import { revise, watch } from "../generated/typespec-v2/clients/typescript/sdk.gen";
import { readFileSync } from "node:fs";
import process from "node:process";

// Q2 modes use the same frozen generated client. The coordinator persists the
// logical key/cursor outside the process, then launches a fresh client to retry.
async function nativeMode(mode: string) {
  const state = JSON.parse(readFileSync(process.env.EC00_NATIVE_STATE!, "utf8"));
  const client = createClient({baseUrl: process.env.EC00_BASE_URL!,
    headers: {"Idempotency-Key": state.key}});
  const controller = new AbortController();
  const failure = process.env.EC00_NATIVE_FAILURE;
  let signal: AbortSignal = controller.signal;
  if (failure === "deadline") {
    signal = AbortSignal.timeout(process.env.EC00_NATIVE_PREDISPATCH === "1" ? 1 : 1000);
    if (process.env.EC00_NATIVE_PREDISPATCH === "1") {
      const keepalive = setTimeout(() => {}, 5000); // AbortSignal's timer is unref'ed in Node.
      await new Promise<void>(resolve => signal.addEventListener("abort", () => resolve(), {once: true}));
      clearTimeout(keepalive);
    }
  } else if (failure === "cancel") {
    if (process.env.EC00_NATIVE_PREDISPATCH === "1") controller.abort();
    else process.stdin.once("data", () => controller.abort());
  }
  const body = {requestId: state.requestId, expectedRevision: state.expectedRevision,
    objective: state.objective};
  try {
    if (mode === "watch") {
      const result = await watch({client, query: {cursor: state.cursor ?? undefined, limit: 2}, signal});
      if (!result.response) throw result.error;
      console.log(JSON.stringify({language: "typescript", status: result.response.status, page: result.data}));
    } else if (mode === "reconcile") {
      const first = await revise({client, path: {requestId: state.requestId}, body});
      const replay = await revise({client, path: {requestId: state.requestId}, body});
      const changed = await revise({client, path: {requestId: state.requestId},
        body: {...body, objective: "changed native effect"}});
      console.log(JSON.stringify({language: "typescript", status: first.response.status,
        first: first.data, replay: replay.data, replayStatus: replay.response.status,
        changedStatus: changed.response.status}));
    } else {
      const result = await revise({client, path: {requestId: state.requestId}, body, signal, throwOnError: true});
      console.log(JSON.stringify({language: "typescript", status: result.response.status, result: result.data}));
    }
  } catch (error) {
    console.log(JSON.stringify({language: "typescript", status: 0,
      error: error instanceof Error ? error.name : String(error)}));
  } finally {
    process.stdin.pause();
  }
}

if (process.env.EC00_NATIVE_MODE) {
  await nativeMode(process.env.EC00_NATIVE_MODE);
  process.exit(0);
}
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
