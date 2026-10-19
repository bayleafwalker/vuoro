import { createServer } from "node:http";
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

const server = createServer(
  connectNodeAdapter({
    routes(router) {
      router.service(OwnerOperations, implementation as never);
    },
  }),
);

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
