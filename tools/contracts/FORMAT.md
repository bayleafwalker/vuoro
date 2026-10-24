# Generated bundle format v1

Normative manifest/integrity formats are wheel package data in
`vuoro_contracts/formats/`. They validate packaging metadata, not domain fields.
The domain operation schemas are independently emitted from TypeSpec.
The proposed [MCP toolset projection requirements](../../docs/plans/2026-10-24-mcp-toolset-projection-requirements.md)
describe a future definition-only mapping. They do not change this manifest
format or admit a public operation.

`operation-manifest.json` records `vuoro-contract-bundle/v1`, explicit contract
identity/version, dialect, schema bundle path and resource ID, source/toolchain
SHA-256 provenance, and a unique operation map. An operation declares method/path,
named request schema or null for absent body, named parameter schemas with location
and presence, every exact HTTP status and its named body schema (null only for
explicit absent body), and explicit internal/public exposure. Required requestMaxBytes and per-status responseMaxBytes are positive UTF-8 byte bounds for JSON bodies, null only for absent bodies; status-map keys match exactly. `default`/ranges,
inline/ambiguous boundaries, unknown schema IDs, duplicate operation IDs or duplicate
method/path/parameter bindings refuse. SDK IDs are identifiers; catalog names remain
separate. Response codes are not guessed from success. No implicit exposure.

`integrity.json` records `vuoro-contract-integrity/v1` and the exact POSIX relative
file -> lowercase SHA-256 inventory, excluding only itself. Include manifest,
OpenAPI, independent schemas, SDK sources and attachments. Reject extra/missing
files, links, escapes and nonregular entries; never ignore an unknown suffix.
Its digest is stored externally by the package consumer/build receipt. Always
verify inventory at load; verify the external digest when supplied. This detects
artifact drift, not semantic meaning or domain canonicalization.

The Python SDK wheel repeats just its own `operation-manifest.json`, OpenAPI
document and schema bundle under `<hashed-contract-package>/data/`, with a nested
`integrity.json` covering those exact files. Its caller supplies the external
digest for that nested inventory. The runtime reads package data through
`importlib.resources`; filesystem and zip resource roots are snapshotted under
the same 16 MiB per-file, 4096-file and 64 MiB total bounds. It does not extract
data next to imported code, where ordinary bytecode could pollute the inventory.
The candidate tree's top-level inventory still covers all generated SDK and
attachment sources.

Owner source comprises main.tsp, explicit tspconfig.yaml and contract.json
(`contractVersion`, exact per-operation `exposures` and `byteBounds`). Metadata may not define
property lists or override emitted schema/status identity. The root generator
accepts --source, --output and --contract-id. --check is default and read-only;
--write is explicit. It verifies selected tool versions/lock/artifact identity,
compiles in a fresh temporary directory, derives manifest, emits Python/TS SDKs
and attachments, then compares complete inventories. No install, network or
ambient executable fallback. Output may not overlap source, escape its admitted
parent or follow links. A write replaces only the admitted generated tree after
successful generation/validation; failure preserves existing output.

F1a runtime/profile and pure manifest/inventory mechanics are the first bounded
unit. F1b compiler/SDK orchestration must additionally prove actual Python and TS
request serialization and all status/body paths using generated SDKs; a pure
schema test cannot close F1b. Q3 upgrade/rollback/supply and F2 real owner, EC02,
positive public MCP, Q2/Q4/Q5 and OA obligations remain open.

Runtime absent body uses exported ABSENT, distinct from JSON null, empty object or
empty text. parse_json(raw, *, max_bytes) requires an explicit positive byte cap.
Known error categories are schema_profile, schema_value, manifest, integrity,
operation, status, parameter, json and body_limit; code/path exclude payload values.
Python and TypeScript apply the same wire-value limits: JSON strings must contain
Unicode scalar values, and integer-valued numbers must fit JavaScript's exact
safe-integer range (−9007199254740991 through 9007199254740991). Encode larger
exact counters as decimal strings so clients preserve them without rounding.

F1a projection rejects path-level inherited parameters and response-object references. They must not be silently omitted or classified as absent bodies. Supporting those semantic forms requires separate qualification. Schema annotation and enum/const data are opaque to reference-edge traversal. Bundle documents are validated from the exact byte snapshot authenticated by the inventory; rereading a changing file must not detach parsed content from its integrity check.

Qualified tool identity is inventoried in toolchain/tool-artifacts.json: installed
Node modules, generation Python site-packages/base stdlib, and exact Node/Python/
Ruff executable hashes. Exclude only __pycache__, .pyc, npm .package-lock.json and
installer RECORD/uv cache/build metadata; they are not execution inputs. Run Python
children with a fresh owned PYTHONPYCACHEPREFIX and PYTHONDONTWRITEBYTECODE=1,
without ambient PYTHONPATH; admit only qualified tool bin paths. Do not trust a
matching version in place of artifact hashes. Verify exact path sets and content
including Node symlink targets; refuse missing/extra/changed executable artifacts.

Restore CPython from the exact build20261001 archive recorded in source-pins,
verify its archive/executable hashes, and select its interpreter explicitly for
both venvs. Do not use the newest same-version uv-managed download: vendor builds
can differ without a Python version change, and uv installation rewrites
_sysconfigdata with the host prefix. This qualified set uses the unmodified
vendor stdlib and retains exact byte/set checks; no host-path normalization or
additional runtime artifact exclusion is allowed.
