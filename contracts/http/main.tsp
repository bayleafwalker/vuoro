import "@typespec/http";
import "../primitives/main.tsp";
using TypeSpec.Http;
namespace Vuoro.Contracts.Http;

// Named domain instantiations determine actual HTTP status, body and error enum.
// No catch-all status or arbitrary success object is supplied here.
model JsonResponse<Status extends int32, Body> {
  @statusCode statusCode: Status;
  @body body: Body;
}
model EmptyResponse<Status extends int32> {
  @statusCode statusCode: Status;
}
model ErrorBody<Code extends string> { code: Code; }
