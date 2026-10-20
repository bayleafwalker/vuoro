import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { createInterface } from "node:readline";
import process from "node:process";
import type { HandlerContext } from "@connectrpc/connect";
import { createClient } from "@connectrpc/connect";
import { connectNodeAdapter, createGrpcTransport } from "@connectrpc/connect-node";
import { OwnerOperations } from "../generated/protobuf/typescript/oa.v1_pb";

const target = process.argv[2];
if (!target) throw new Error("upstream gRPC target positional argument required");

const upstream = createClient(
  OwnerOperations,
  createGrpcTransport({
    baseUrl: `http://${target}`,
    defaultTimeoutMs: 5_000,
  }),
);

type ForwardCall = (
  request: unknown,
  options: { signal?: AbortSignal; timeoutMs?: number; headers?: HeadersInit },
) => Promise<unknown>;
type DynamicImplementation = Record<
  string,
  (request: unknown, context: HandlerContext) => Promise<unknown>
>;

const upstreamMethods = upstream as unknown as Record<string, ForwardCall>;
const implementation: DynamicImplementation = {};
for (const method of OwnerOperations.methods) {
  if (method.methodKind !== "unary") {
    throw new Error(`unsupported non-unary method: ${method.localName}`);
  }
  const call = upstreamMethods[method.localName];
  if (!call) throw new Error(`missing upstream method: ${method.localName}`);
  implementation[method.localName] = (request, context) => {
    const remaining = context.timeoutMs();
    // The disposable authenticated fixture verifies these at the owner. The
    // forwarding edge does not turn unsigned actor/authority headers into identity.
    const headers = new Headers();
    for (const name of ["idempotency-key", "x-vuoro-identity", "x-request-id"]) {
      const value = context.requestHeader.get(name);
      if (value !== null) headers.set(name, value);
    }
    return call(request, {
      signal: context.signal,
      timeoutMs: Math.min(remaining ?? 5_000, 5_000),
      headers,
    });
  };
}

// Fetch-style `Headers.get` joins repeated fields with ", ", which would turn two
// assertions into one malformed value. Repeated identity or correlation fields
// are ambiguous, so they are counted in the raw header list and refused here,
// before any upstream call, with the same unauthenticated outcome as the owner.
const SINGLE_VALUED = new Set(["x-vuoro-identity", "x-request-id"]);
const DUPLICATE_REFUSAL = "duplicate identity or correlation header refused";

function repeatedSingleValued(rawHeaders: string[]): string | undefined {
  const seen = new Set<string>();
  for (let index = 0; index < rawHeaders.length; index += 2) {
    const name = rawHeaders[index]!.toLowerCase();
    if (!SINGLE_VALUED.has(name)) continue;
    if (seen.has(name)) return name;
    seen.add(name);
  }
  return undefined;
}

const adapter = connectNodeAdapter({
  routes(router) {
    router.service(OwnerOperations, implementation as never);
  },
});

const server = createServer((request: IncomingMessage, response: ServerResponse) => {
  const repeated = repeatedSingleValued(request.rawHeaders);
  if (repeated === undefined) {
    adapter(request, response);
    return;
  }
  // Connect unary error shape: HTTP 401 with a JSON {code, message} body.
  const body = JSON.stringify({ code: "unauthenticated", message: DUPLICATE_REFUSAL });
  request.resume();
  response.writeHead(401, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    "x-ec00-edge-refusal": "duplicate-header",
  });
  response.end(body);
});

server.listen(0, "127.0.0.1", () => {
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("could not determine local listener address");
  }
  process.stdout.write(`${JSON.stringify({ base_url: `http://127.0.0.1:${address.port}` })}\n`);
});

createInterface({ input: process.stdin }).once("line", () => {
  server.close();
});
