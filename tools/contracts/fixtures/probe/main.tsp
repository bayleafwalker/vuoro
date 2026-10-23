// Generic supporting source only, not a product API or owner fixture.
import "@typespec/http";
import "@typespec/json-schema";
import "@vuoro/contracts";
using TypeSpec.Http;
using TypeSpec.JsonSchema;
using Vuoro.Contracts;
using Vuoro.Contracts.Http;
@service(#{ title: "Pure projection probe" })
@jsonSchema
namespace Probe {
  model Nested { label: string; }
  model Input { revision: NonnegativeInt64Decimal; enabled: boolean; note?: string | null; nested: Nested; }
  model Metadata is Record<unknown>;
  model Result { state: "ready"; revision: NonnegativeInt64Decimal; metadata?: Metadata; }
  model InvalidBody { code: "invalid"; }
  model UnavailableBody { code: "unavailable"; }
  union NullableResult { value: Result, nil: null }
  @minLength(1) @maxLength(64) scalar Key extends string;
  model Ok is JsonResponse<200, Result>;
  @error model Invalid is JsonResponse<400, InvalidBody>;
  @error model Unavailable is JsonResponse<503, UnavailableBody>;
  @route("/probe/{key}") @post
  op submit(@path key: Key, @body input: Input): Ok | Invalid | Unavailable;
  @route("/probe/{key}") @get
  op inspect(@path key: Key): Ok;
  @route("/probe/{key}/empty") @get
  op empty(@path key: Key): EmptyResponse<204>;
  @route("/probe/{key}/null") @get
  op nullBody(@path key: Key): JsonResponse<200, NullableResult>;
}
