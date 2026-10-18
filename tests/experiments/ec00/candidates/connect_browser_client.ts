import { fromJson, toJson, type JsonValue } from "@bufbuild/protobuf";
import { ConnectError, createClient } from "@connectrpc/connect";
import { createConnectTransport } from "@connectrpc/connect-web";
import { createClient as createHttpClient } from "../generated/typespec-v2/clients/typescript/client";
import * as httpSdk from "../generated/typespec-v2/clients/typescript/sdk.gen";
import { OwnerOperations } from "../generated/protobuf/typescript/oa.v1_pb";

declare global {
  interface Window {
    ec00Rpc: (
      baseUrl: string,
      binary: boolean,
      methodName: string,
      input: unknown,
      idemKey?: string,
      timeoutMs?: number,
      cancelAfterMs?: number,
    ) => Promise<
      | { ok: true; value: JsonValue }
      | { ok: false; code: ConnectError["code"]; message: string }
    >;
    ec00Http: (
      baseUrl: string,
      methodName: string,
      input: unknown,
      idemKey?: string,
      timeoutMs?: number,
      cancelAfterMs?: number,
    ) => Promise<{ ok: boolean; status: number; value: unknown; errorName?: string }>;
  }
}

type DynamicCall = (
  request: unknown,
  options: { timeoutMs: number; headers: HeadersInit; signal?: AbortSignal },
) => Promise<unknown>;

window.ec00Rpc = async (
  baseUrl: string,
  binary: boolean,
  methodName: string,
  input: unknown,
  idemKey = "",
  timeoutMs = 5_000,
  cancelAfterMs = 0,
) => {
  const controller = new AbortController();
  const cancelTimer = cancelAfterMs > 0
    ? setTimeout(() => controller.abort(), cancelAfterMs)
    : undefined;
  try {
    const method = OwnerOperations.method[methodName];
    if (!method) throw new Error(`unknown OwnerOperations method: ${methodName}`);

    const request = fromJson(method.input, input as JsonValue);
    const client = createClient(
      OwnerOperations,
      createConnectTransport({ baseUrl, useBinaryFormat: binary }),
    );
    const call = (client as unknown as Record<string, DynamicCall>)[method.localName];
    if (!call) throw new Error(`missing generated client method: ${method.localName}`);
    const response = await call(request, {
      timeoutMs,
      headers: { "idempotency-key": idemKey },
      signal: controller.signal,
    });
    return {
      ok: true,
      value: toJson(method.output, response as never, { alwaysEmitImplicit: true }),
    };
  } catch (error) {
    const connectError = ConnectError.from(error);
    return {
      ok: false,
      code: connectError.code,
      message: connectError.rawMessage,
    };
  } finally {
    if (cancelTimer !== undefined) clearTimeout(cancelTimer);
  }
};

type HttpCall = (options: Record<string, unknown>) => Promise<{
  data?: unknown;
  error?: unknown;
  response?: Response;
}>;

window.ec00Http = async (
  baseUrl,
  methodName,
  input,
  idemKey = "",
  timeoutMs = 5_000,
  cancelAfterMs = 0,
) => {
  const controller = new AbortController();
  const cancelTimer = cancelAfterMs > 0
    ? setTimeout(() => controller.abort(), cancelAfterMs)
    : undefined;
  try {
    const client = createHttpClient({ baseUrl });
    const call = (httpSdk as unknown as Record<string, HttpCall>)[methodName];
    const value = input as Record<string, unknown>;
    const options: Record<string, unknown> = {
      client,
      headers: { "idempotency-key": idemKey },
      signal: AbortSignal.any([AbortSignal.timeout(timeoutMs), controller.signal]),
    };

    if (methodName === "watch" || methodName === "listRequests") {
      options.query = input;
    } else if (methodName === "readRequest" || methodName === "readDependants" || methodName === "readArtifact") {
      options.path = methodName === "readArtifact"
        ? { requestId: value.requestId, materialRevision: value.materialRevision }
        : { requestId: value.requestId };
    } else {
      options.body = input;
      options.path = { requestId: value.requestId };
    }

    const result = await call(options);
    return {
      ok: result.error === undefined && !!result.response,
      status: result.response?.status ?? 0,
      value: result.data ?? result.error ?? null,
      errorName: result.error instanceof Error ? result.error.name : undefined,
    };
  } catch (error) {
    if (!(error instanceof TypeError)) throw error;
    return {
      ok: false,
      status: 0,
      value: null,
      errorName: error.name,
    };
  } finally {
    if (cancelTimer !== undefined) clearTimeout(cancelTimer);
  }
};
