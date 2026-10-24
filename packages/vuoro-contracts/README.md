# vuoro-contracts

Pure runtime for owner-packaged, generated JSON Schema operation boundaries.
This package owns no domain state, network client, SQL, authorization or transport.
It consumes an operation manifest and independent JSON Schema 2020-12 bundle.
The F1 profile, validation, production compiler and native SDK attachments pass
local source qualification. Canonical CI and real-owner F2 remain separate gates.

`ContractBundle.load(root, integrity_sha256=...)` accepts a filesystem directory
or an `importlib.resources` root. It snapshots the complete tree within fixed
file-count and byte limits, verifies the exact inventory, and checks the
externally supplied integrity digest. Resource roots are read directly without
extracting files beside installed Python modules, so ordinary `__pycache__`
creation cannot change contract data inventory. A digest supplied from the same
untrusted directory is not a trust anchor.
`validate_request`, `validate_response`, and `validate_parameter` preserve the
original value without coercion, defaults or removal. `parse_json` refuses duplicate
keys, nonfinite numbers and invalid UTF-8 before a handler can be invoked.
Errors expose stable safe codes and schema paths, never input values.

The runtime wheel ships only its JSON Schema format documents. Each generated
Python SDK carries its own authenticated manifest, OpenAPI document and schema
bundle under `<contract package>/data`; it depends on the separately installed
`vuoro-contracts` runtime and does not copy that runtime or share a fixed SDK
module namespace. The compiler/SDK gate exercises real TypeSpec emission and
generated Python/TS requests through bounded in-process transports. It supplies
no server or owner qualification; actual owner state and deployment require F2.
