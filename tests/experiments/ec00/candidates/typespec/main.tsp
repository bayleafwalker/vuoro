import "@typespec/http";
import "@typespec/openapi";

using TypeSpec.Http;

@service(#{ title: "EC-00 disposable OA candidate" })
@route("/v1")
namespace Ec00.Oa.V1 {
  model PublishRequest {
    /** Decimal string identifier; preserved above JavaScript's safe integer range. */
    requestId: string;
    objective: string;
    note?: string | null;
    dependants?: string[];
    artifactDigest?: string;
    artifactKeyId?: string;
    artifactBytes?: bytes;
    artifactEnvelope?: bytes;
    artifactSignature?: bytes;
  }

  model ReviseRequest {
    /** Decimal string identifier; preserved above JavaScript's safe integer range. */
    requestId: string;
    expectedRevision?: int64 | null;
    objective: string;
    note?: string | null;
  }

  model MutationResult {
    requestId: string;
    revision: int64;
  }

  model RevisionRequest { expectedRevision: int64; }
  model WithdrawRequest { dependentId: string; expectedRevision: int64; }
  model VerifyRequest { expectedRevision: int64; evidenceRef: string; }
  model DecideArtifactRequest { expectedRevision: int64; artifactDigest: string; }
  model AcknowledgeRequest { dependentId: string; expectedRevision: int64; }
  model ReadResult { json: string; }
  model PageRequest { cursor?: string; limit: int32; }

  @route("/requests")
  @post
  op publish(@body input: PublishRequest): MutationResult;

  @route("/requests/{requestId}")
  @patch
  op revise(@path requestId: string, @body input: ReviseRequest): MutationResult;

  @route("/requests/{requestId}")
  @get
  op readRequest(@path requestId: string): ReadResult;

  @route("/requests/{requestId}/dependants")
  @get
  op readDependants(@path requestId: string): ReadResult;

  @route("/requests/{requestId}/artifact/{materialRevision}")
  @get
  op readArtifact(@path requestId: string, @path materialRevision: int64): ReadResult;

  @route("/requests")
  @get
  op listRequests(@query cursor?: string, @query limit?: int32): ReadResult;

  @route("/events")
  @get
  op watch(@query cursor?: string, @query limit?: int32): ReadResult;

  @route("/requests/{requestId}/withdraw")
  @post
  op withdrawDependent(@path requestId: string, @body input: WithdrawRequest): MutationResult;

  @route("/requests/{requestId}/report")
  @post
  op reportPerformed(@path requestId: string, @body input: RevisionRequest): MutationResult;

  @route("/requests/{requestId}/verify")
  @post
  op verify(@path requestId: string, @body input: VerifyRequest): MutationResult;

  @route("/requests/{requestId}/artifact-decision")
  @post
  op decideArtifact(@path requestId: string, @body input: DecideArtifactRequest): MutationResult;

  @route("/requests/{requestId}/resolve")
  @post
  op resolve(@path requestId: string, @body input: RevisionRequest): MutationResult;

  @route("/requests/{requestId}/acknowledge")
  @post
  op acknowledgeContinuation(@path requestId: string, @body input: AcknowledgeRequest): MutationResult;
}
