import "@typespec/json-schema";
using TypeSpec.JsonSchema;

namespace Vuoro.Contracts.OperatorRequests;

@jsonSchema
@pattern("^req_[0-9A-HJKMNP-TV-Z]{26}$")
scalar RequestId extends string;

enum RequestKind {
  perform,
  authorize,
  decide,
  provideInformation: "provide-information",
}

enum RequestState {
  open,
  reportedPerformed: "reported-performed",
  verifying,
  resolved,
  withdrawn,
  superseded,
}

enum RequestFreshness {
  current,
  needsRefresh: "needs-refresh",
  unknownValue: "unknown",
}

model SourceRevision {
  source: string;
  revision: string;
}

model RequiredInput {
  name: string;
  acquire_from: string;
}

model ExecutionContext {
  host: string;
  directory: string;
  intended_identity: string;
  target: string;
  inputs: RequiredInput[];
}

model ProcedureStep {
  step_id: string;
  instruction: string;
  expected_result: string;
  preconditions: string[];
  verification: string;
  partial_progress: string;
  recovery: string;
}

model Procedure {
  version: string;
  steps: ProcedureStep[];
}

model CheckedBlocker {
  condition: string;
  checked_at: string;
  fresh_until: string;
  source_revisions: SourceRevision[];
}

@jsonSchema
model RequestPacket {
  schema: "operator-request/v1";
  kind: RequestKind;
  required_outcome: string;
  reason: string;
  execution: ExecutionContext;
  procedure: Procedure;
  blocker: CheckedBlocker;
}

model Dependant {
  work_item_id: int64;
  session_id: string;
  runtime_id?: string;
}

model PublishRequest {
  request_id: RequestId;
  packet: RequestPacket;
  dependants: Dependant[];
  idempotency_key: string;
}

model ReviseRequest {
  request_id: RequestId;
  expected_revision: int32;
  packet: RequestPacket;
  idempotency_key: string;
}

model ChangeDependant {
  request_id: RequestId;
  expected_revision: int32;
  dependant: Dependant;
  idempotency_key: string;
}

model WithdrawDependant {
  request_id: RequestId;
  expected_revision: int32;
  work_item_id: int64;
  session_id: string;
  idempotency_key: string;
}

model GetRequest {
  request_id: RequestId;
}

model ListRequests {
  limit: int32;
  after_request_id?: RequestId;
  state?: RequestState;
}

enum DependantState {
  active,
  withdrawn,
}

model DependantRecord extends Dependant {
  state: DependantState;
  work_revision: string;
  attached_at: string;
  withdrawn_at: string | null;
}

model RequestSnapshot {
  request_id: RequestId;
  revision: int32;
  state: RequestState;
  freshness: RequestFreshness;
  packet: RequestPacket;
  dependants: DependantRecord[];
  created_at: string;
  updated_at: string;
}

model MutationResult {
  request_id: RequestId;
  revision: int32;
  replayed: boolean;
}

model ListRequestsResult {
  requests: RequestSnapshot[];
  next_after_request_id: RequestId | null;
}
