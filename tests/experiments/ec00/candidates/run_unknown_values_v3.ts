// Qualification runner: invoke actual generated SDKs; no hand-authored rules.
import { createClient } from "../generated/typespec-v3/clients/typescript/client";
import * as validated from "../generated/typespec-v3/clients/typescript/validated-sdk";
import * as raw from "../generated/typespec-v3/clients/typescript/sdk.gen";
const baseUrl = process.env.EC00_BASE_URL;
if (!baseUrl || process.env.EC00_UNKNOWN_VALUES_V3 !== "1") {
  throw new Error("EC00_BASE_URL and EC00_UNKNOWN_VALUES_V3=1 are required");
}
const client = createClient({ baseUrl });
const requestId = "9007199254741199";
const sdk = process.env.EC00_V3_VALIDATOR_FALSIFIER === "omit" ? raw : validated;
async function observe(operation: () => Promise<unknown>) {
  try {
    const result = await operation() as { data?: unknown; response: Response };
    return result.response.status === 200 && result.data !== undefined
      ? { decoded: true, value: result.data }
      : { decoded: false, error: `HTTP ${result.response.status}` };
  } catch (error) {
    return { decoded: false, error: error instanceof Error ? error.name : "decoder error" };
  }
}
async function main() {
  console.log(JSON.stringify({
    language: "typescript",
    request: await observe(() => sdk.readRequest({ client, path: { requestId } })),
    artifact: await observe(() => sdk.readArtifact({ client, path: { requestId, materialRevision: "1" } })),
  }));
}
main().catch(error => { console.error(error); process.exit(1); });
