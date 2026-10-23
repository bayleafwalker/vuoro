// Disposable v3 qualification source. Do not edit the frozen v1/v2 inputs.
import "@typespec/http";
import "@typespec/json-schema";

using TypeSpec.Http;
using TypeSpec.JsonSchema;

@service(#{ title: "EC-00 schema-validated OA candidate v3" })
@route("/v3")
@jsonSchema
namespace Ec00.Oa.V3 {
  @pattern("^[1-9][0-9]{0,39}$(?![\\s\\S])")
  scalar RequestId extends string;

  // Canonical decimal strings in [0, signed-int64-max]. The pattern is the
  // authored wire constraint; no runtime validator maintains a second bound.
  @pattern("^(0|[1-9][0-9]{0,17}|[1-8][0-9]{18}|9[0-1][0-9]{17}|92[0-1][0-9]{16}|922[0-2][0-9]{15}|9223[0-2][0-9]{14}|92233[0-6][0-9]{13}|922337[0-1][0-9]{12}|92233720[0-2][0-9]{10}|922337203[0-5][0-9]{9}|9223372036[0-7][0-9]{8}|92233720368[0-4][0-9]{7}|922337203685[0-3][0-9]{6}|9223372036854[0-6][0-9]{5}|92233720368547[0-6][0-9]{4}|922337203685477[0-4][0-9]{3}|9223372036854775[0-7][0-9]{2}|922337203685477580[0-6]|9223372036854775807)$(?![\\s\\S])")
  scalar Revision extends string;

  @minLength(1) @maxLength(256)
  scalar IdempotencyKey extends string;

  // The sealed emitter closes each input, including the real nested body.
  model CommandBody {
    @maxLength(65536) objective: string;
    @maxLength(65536) note?: string | null;
  }
  model SignedArtifactInput {
    artifactDigest: string;
    artifactKeyId: string;
    artifactBytes: bytes;
    artifactEnvelope: bytes;
    artifactSignature: bytes;
  }
  model PublishRequest {
    requestId: RequestId;
    body: CommandBody;
    @minItems(1) @maxItems(64) dependants: string[];
    artifact?: SignedArtifactInput;
  }
  model ReviseRequest {
    requestId: RequestId;
    expectedRevision: Revision;
    body: CommandBody;
  }
  enum RequestState { open, resolved }

  // Explicit open metadata is deliberate. Known properties remain constrained;
  // unknown lifecycle values are not made legal by this indexer.
  model RequestBody {
    ...Record<unknown>;
    objective: string;
    note?: string | null;
  }
  model MutationResult {
    ...Record<unknown>;
    requestId: RequestId;
    revision: Revision;
  }
  model RequestRead {
    ...Record<unknown>;
    requestId: RequestId;
    revision: Revision;
    materialRevision: Revision;
    state: RequestState;
    body: RequestBody;
    reported: boolean;
    verified: boolean;
    evidenceRef?: string | null;
    artifactDecisionDigest?: string | null;
    createdSeq: Revision;
  }
  model ArtifactRead {
    ...Record<unknown>;
    materialRevision: Revision;
    materialDigest: string;
    artifactDigest: string;
    keyId: string;
    originalBytes: bytes;
    envelopeBytes: bytes;
    signature: bytes;
  }

  @route("/requests") @post
  op publish(@header("Idempotency-Key") idempotencyKey: IdempotencyKey, @body input: PublishRequest): MutationResult;
  @route("/requests/{requestId}") @patch
  op revise(@path requestId: RequestId, @header("Idempotency-Key") idempotencyKey: IdempotencyKey, @body input: ReviseRequest): MutationResult;
  @route("/requests/{requestId}") @get
  op readRequest(@path requestId: RequestId): RequestRead;
  @route("/requests/{requestId}/artifact/{materialRevision}") @get
  op readArtifact(@path requestId: RequestId, @path materialRevision: Revision): ArtifactRead;
}
