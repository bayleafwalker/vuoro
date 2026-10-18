// Disposable versioned comparison; v1 stays a separate supported experiment input.
import "./main.tsp";

using TypeSpec.Http;

@service(#{ title: "EC-00 disposable OA candidate v2" })
@route("/v2")
namespace Ec00.Oa.V2 {
  model PublishRequest is Ec00.Oa.V1.PublishRequest;
  model ReviseRequest is Ec00.Oa.V1.ReviseRequest;
  model MutationResult is Ec00.Oa.V1.MutationResult;
  model RevisionRequest is Ec00.Oa.V1.RevisionRequest;
  model WithdrawRequest is Ec00.Oa.V1.WithdrawRequest;
  model VerifyRequest is Ec00.Oa.V1.VerifyRequest;
  model DecideArtifactRequest is Ec00.Oa.V1.DecideArtifactRequest;
  model AcknowledgeRequest is Ec00.Oa.V1.AcknowledgeRequest;
  model RequestBody is Ec00.Oa.V1.RequestBody;
  model RequestRead is Ec00.Oa.V1.RequestRead;
  model DependentRead is Ec00.Oa.V1.DependentRead;
  model DependantsRead is Ec00.Oa.V1.DependantsRead;
  model ArtifactRead is Ec00.Oa.V1.ArtifactRead;
  model RequestSummary is Ec00.Oa.V1.RequestSummary;
  model RequestPage is Ec00.Oa.V1.RequestPage;
  model WatchEvent is Ec00.Oa.V1.WatchEvent;
  model WatchPage is Ec00.Oa.V1.WatchPage;
  model PageRequest is Ec00.Oa.V1.PageRequest;

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

// Encoding changes belong to the new version, not the imported v1 contract.
@@encode(Ec00.Oa.V2.ReviseRequest.expectedRevision, string);
@@encode(Ec00.Oa.V2.RevisionRequest.expectedRevision, string);
@@encode(Ec00.Oa.V2.WithdrawRequest.expectedRevision, string);
@@encode(Ec00.Oa.V2.VerifyRequest.expectedRevision, string);
@@encode(Ec00.Oa.V2.DecideArtifactRequest.expectedRevision, string);
@@encode(Ec00.Oa.V2.AcknowledgeRequest.expectedRevision, string);
@@encode(Ec00.Oa.V2.MutationResult.revision, string);

// Domain counter constraints remain authored beside their wire encoding.
@@minValue(Ec00.Oa.V2.ReviseRequest.expectedRevision, 0);
@@doc(Ec00.Oa.V2.ReviseRequest.expectedRevision, "Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.");
@@minValue(Ec00.Oa.V2.RevisionRequest.expectedRevision, 0);
@@doc(Ec00.Oa.V2.RevisionRequest.expectedRevision, "Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.");
@@minValue(Ec00.Oa.V2.WithdrawRequest.expectedRevision, 0);
@@doc(Ec00.Oa.V2.WithdrawRequest.expectedRevision, "Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.");
@@minValue(Ec00.Oa.V2.VerifyRequest.expectedRevision, 0);
@@doc(Ec00.Oa.V2.VerifyRequest.expectedRevision, "Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.");
@@minValue(Ec00.Oa.V2.DecideArtifactRequest.expectedRevision, 0);
@@doc(Ec00.Oa.V2.DecideArtifactRequest.expectedRevision, "Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.");
@@minValue(Ec00.Oa.V2.AcknowledgeRequest.expectedRevision, 0);
@@doc(Ec00.Oa.V2.AcknowledgeRequest.expectedRevision, "Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.");
@@minValue(Ec00.Oa.V2.MutationResult.revision, 0);
@@doc(Ec00.Oa.V2.MutationResult.revision, "Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.");
