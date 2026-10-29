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
  declined,
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

enum ProgressStatus {
  inProgress: "in-progress",
  completed,
  blocked,
  uncertain,
}

enum PerformedOutcome {
  performed,
  partial,
  uncertain,
}

model RecordProcedureProgress {
  request_id: RequestId;
  expected_revision: int32;
  work_item_id: int64;
  session_id: string;
  step_id: string;
  status: ProgressStatus;
  note: string;
  idempotency_key: string;
}

model ReportPerformed {
  request_id: RequestId;
  expected_revision: int32;
  outcome: PerformedOutcome;
  note: string;
  idempotency_key: string;
}

model HumanAttestation {
  request_id: RequestId;
  expected_revision: int32;
  statement: string;
  performed_at: string;
  idempotency_key: string;
}

model DeclineRequest {
  request_id: RequestId;
  expected_revision: int32;
  reason: string;
  idempotency_key: string;
}

model SupersedeRequest {
  request_id: RequestId;
  expected_revision: int32;
  replacement_request_id: RequestId;
  packet: RequestPacket;
  idempotency_key: string;
}

model GetRequest {
  request_id: RequestId;
}

model ListRequestEvents {
  request_id: RequestId;
  after_revision: int32;
  limit: int32;
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

model DependantRecord {
  work_item_id: int64;
  session_id: string;
  runtime_id: string | null;
  state: DependantState;
  work_revision: string;
  current_work_revision: string | null;
  source_changed: boolean;
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

model RequestEvent {
  request_id: RequestId;
  revision: int32;
  action: string;
  actor: string;
  details: Record<unknown>;
  recorded_at: string;
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

model ListRequestEventsResult {
  request_id: RequestId;
  events: RequestEvent[];
  next_after_revision: int32;
}

model ContinuationOwner {
  @minLength(1)
  repo_id: string;
  read_operation: "work.request.continuation-v1";
}

model ContinuationResolution {
  @minValue(1)
  event_revision: int32;
  basis: "human-attestation";
  event_recorded_at: utcDateTime;
}

@minLength(1)
scalar ContinuationWorkRevision extends string;

model ContinuationDependant {
  @minValue(1)
  @maxValue(9007199254740991)
  work_item_id: safeint;
  @minLength(1)
  @maxLength(256)
  session_id: string;
  work_revision: ContinuationWorkRevision;
  current_work_revision: ContinuationWorkRevision | null;
  state: "continuation-ready" | "needs-refresh";
}

/** One owner snapshot; reading neither acknowledges delivery nor wakes a runtime. */
@jsonSchema
model RequestContinuationReady {
  schema: "operator-request-continuation-ready/v1";
  owner: ContinuationOwner;
  request_id: RequestId;
  @minValue(1)
  request_revision: int32;
  resolution: ContinuationResolution;
  @maxItems(128)
  dependants: ContinuationDependant[];
}
