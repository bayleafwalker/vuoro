# vuoro-contracts

Pure runtime for owner-packaged, generated JSON Schema operation boundaries.
This package owns no domain state, network client, SQL, authorization or transport.
It consumes an operation manifest and independent JSON Schema 2020-12 bundle.
The F1 profile, validation, production compiler and native SDK attachments pass
local source qualification. Canonical CI and real-owner F2 remain separate gates.

`ContractBundle.load(root, integrity_sha256=...)` verifies an externally supplied
integrity digest when provided, and always checks the complete output inventory.
A digest supplied from the same untrusted directory is not a trust anchor.
`validate_request`, `validate_response`, and `validate_parameter` preserve the
original value without coercion, defaults or removal. `parse_json` refuses duplicate
keys, nonfinite numbers and invalid UTF-8 before a handler can be invoked.
Errors expose stable safe codes and schema paths, never input values.

The owner packages generated assets. No domain schema is shipped in this wheel.
The compiler/SDK gate exercises real TypeSpec emission and generated Python/TS
requests through bounded in-process transports. It supplies no server or owner
qualification; actual owner state and deployment require F2.
