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

  @route("/requests")
  @post
  op publish(@body input: PublishRequest): MutationResult;

  @route("/requests/{requestId}")
  @patch
  op revise(@path requestId: string, @body input: ReviseRequest): MutationResult;
}
