# vuoro-contracts

Pure runtime for owner-packaged, generated JSON Schema operation boundaries.
This package owns no domain state, network client, SQL, authorization or transport.
It consumes an operation manifest and independent JSON Schema 2020-12 bundle.
The profile and validation implementation are deliberately pending F1.

`ContractBundle.load(root, integrity_sha256=...)` verifies an externally supplied
integrity digest when provided, and always checks the complete output inventory.
A digest supplied from the same untrusted directory is not a trust anchor.
`validate_request`, `validate_response`, and `validate_parameter` preserve the
original value without coercion, defaults or removal. `parse_json` refuses duplicate
keys, nonfinite numbers and invalid UTF-8 before a handler can be invoked.
Errors expose stable safe codes and schema paths, never input values.

The owner packages generated assets. No domain schema is shipped in this wheel.
Source-only preparation is not generation, SDK, server or owner qualification.
