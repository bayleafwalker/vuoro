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
  enum RequestState {
    open,
    resolved,
  }
  model RequestBody { objective: string; note?: string | null; }
  model RequestRead {
    requestId: string;
    @encode(string) revision: int64;
    @encode(string) materialRevision: int64;
    state: RequestState;
    body: RequestBody;
    reported: boolean;
    verified: boolean;
    evidenceRef?: string | null;
    artifactDecisionDigest?: string | null;
    @encode(string) createdSeq: int64;
  }
  model DependentRead { dependentId: string; active: boolean; continuationAck: boolean; }
  model DependantsRead { items: DependentRead[]; }
  model ArtifactRead {
    @encode(string) materialRevision: int64;
    materialDigest: string;
    artifactDigest: string;
    keyId: string;
    originalBytes: bytes;
    envelopeBytes: bytes;
    signature: bytes;
  }
  model RequestSummary {
    requestId: string;
    @encode(string) revision: int64;
    state: RequestState;
    @encode(string) createdSeq: int64;
  }
  model RequestPage { items: RequestSummary[]; nextCursor?: string | null; }
  model WatchEvent {
    @encode(string) seq: int64;
    requestId: string;
    action: string;
    @encode(string) revision: int64;
  }
  model WatchPage { events: WatchEvent[]; nextCursor?: string | null; more: boolean; }
  model PageRequest { cursor?: string; limit: int32; }

  @route("/requests")
  @post
  op publish(@body input: PublishRequest): MutationResult;

  @route("/requests/{requestId}")
  @patch
  op revise(@path requestId: string, @body input: ReviseRequest): MutationResult;

  @route("/requests/{requestId}")
  @get
  op readRequest(@path requestId: string): RequestRead;

  @route("/requests/{requestId}/dependants")
  @get
  op readDependants(@path requestId: string): DependantsRead;

  @route("/requests/{requestId}/artifact/{materialRevision}")
  @get
  op readArtifact(@path requestId: string, @path @encode(string) materialRevision: int64): ArtifactRead;

  @route("/requests")
  @get
  op listRequests(@query cursor?: string, @query limit?: int32): RequestPage;

  @route("/events")
  @get
  op watch(@query cursor?: string, @query limit?: int32): WatchPage;

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
