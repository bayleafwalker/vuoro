// Characterize the existing generated decoder. Do not add a runtime schema here.
import { createClient } from "../generated/typespec-v2/clients/typescript/client";
import { readRequest, readArtifact } from "../generated/typespec-v2/clients/typescript/sdk.gen";

const baseUrl = process.env.EC00_BASE_URL;
if (!baseUrl || process.env.EC00_UNKNOWN_VALUES !== "1") {
  throw new Error("EC00_BASE_URL and EC00_UNKNOWN_VALUES=1 are required");
}
const client = createClient({ baseUrl });
const requestId = "9007199254741199";
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
console.log(JSON.stringify({
  language: "typescript",
  request: await observe(() => readRequest({ client, path: { requestId } })),
  artifact: await observe(() => readArtifact({ client, path: { requestId, materialRevision: "1" } })),
}));
