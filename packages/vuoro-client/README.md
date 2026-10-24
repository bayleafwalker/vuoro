# Vuoro client

Transport-only Python client for Vuoro governed-work services. The project
overview and architecture documentation live in the repository root.

# Provisional Sovite remote execution client

`vuoro_client.sovite_execution.SoviteExecutionClient` is a standalone,
provisional adapter for the routes in the local Sovite candidate pinned at
`ca9b5df3caef28d6dca0dd587e251aff482dcf11` (`sovite/execution_api.py` and
`sovite/remote_execution.py`). It covers `pull`, `start`, `renew`, `status`,
`handback`, `complete`, and `lookup` under `/internal/execution/`. It is not
connected to `AsyncVuoroClient`, service composition, or any live worker path.

The bearer is supplied directly for this execution-owner API and is sent only
as an Authorization header to the configured base endpoint; it is not included
in requests, errors, or logs. Requests and streamed response bodies have byte
bounds, response models reject unknown fields, and the client does not retry.
An ambiguous mutation transport failure raises
`SoviteExecutionUncertain` with the operation ID and directs the caller to
`lookup(instance_id, operation_id)` before retrying or advancing.

This adapter is provisional source integration only. The pinned Sovite source
has not been qualified against this client. Before any runtime adoption, verify
all seven route/request/response shapes against that exact source, exercise
bounded streaming and malformed/oversized payload refusals, verify token scope
and redirect behavior, and qualify uncertain-outcome lookup behavior. Update
the source pin if the Sovite contract changes.
