# Generated bundle format v1

Normative manifest/integrity formats are wheel package data in
`vuoro_contracts/formats/`. They validate packaging metadata, not domain fields.
The domain operation schemas are independently emitted from TypeSpec.
The [MCP toolset projection requirements](../../docs/plans/2026-10-24-mcp-toolset-projection-requirements.md)
define the source-authored mapping and deterministic tool artifact described below.
The current contract source contains no authored HTTP operations, so its generated
tool list is empty by architecture; the operator-request TypeSpec models remain
types and do not become tools or handlers by inference.

`operation-manifest.json` records `vuoro-contract-bundle/v1`, explicit contract
identity/version, dialect, schema bundle path and resource ID, source/toolchain
SHA-256 provenance, and a unique operation map. An operation declares method/path,
named request schema or null for absent body, named parameter schemas with location
and presence, every exact HTTP status and its named body schema (null only for
explicit absent body), and explicit internal/public exposure. Required requestMaxBytes and per-status responseMaxBytes are positive UTF-8 byte bounds for JSON bodies, null only for absent bodies; status-map keys match exactly. `default`/ranges,
inline/ambiguous boundaries, unknown schema IDs, duplicate operation IDs or duplicate
method/path/parameter bindings refuse. SDK IDs are identifiers; catalog names remain
separate. Response codes are not guessed from success. No implicit exposure.

Owner `contract.json` may include `mcpTools`, an object keyed by the exact
OpenAPI `operationId` set whose `exposures` value is `public`. Each entry has
exactly `name`, `title`, and `description`; names are stable explicit identifiers
matching `^[a-z][a-z0-9_]{0,63}$`, titles are 1..100 characters and descriptions
1..500 characters. Missing, extra, duplicate-name, internal-operation, or
malformed entries refuse the entire projection. No mapping is inferred from a
path, HTTP verb, SDK name, or schema title. When no operation is public, the
mapping may be omitted or empty.

The generator emits `mcp-tools.json` with the tool metadata, a closed
`inputSchema`, and a private reversible binding plan. Path and query parameters
become named arguments; a JSON object request body contributes its properties;
a non-object body uses the single `body` argument. Header parameters, argument
name collisions, open object bodies, and unsupported top-level body keywords
refuse projection. Input schemas retain only the transitive schema-resource
closure needed by their arguments, with named references rebased locally;
cycles, including self-references, refuse the projection. Path/query parameters
are limited to scalar string, integer, number, or boolean schemas, using path
`simple`/non-exploded and query `form`/exploded serialization with
`allowReserved: false`. The generator normalizes these defaults into the
manifest and rejects parameter content, non-default serialization, and
array/object/composed parameter schemas. The binding metadata retains these
serialization fields for path and query arguments.
The binding plan records each argument's source location and schema ID plus the
operation ID, method, and path; it is generation metadata, not caller input or
authorization. The artifact carries the exact source/toolchain, manifest and
schema-bundle digests and projection-requirements version. `mcp-tools.json` is
included in the top-level integrity inventory and Python SDK package data.

The projection schema vocabulary is explicitly bounded to Draft 2020-12 core,
validation, and annotation keywords: `$ref`, `$schema`, `$id`, `$anchor`,
`$comment`, `$defs`, `title`, `description`, `default`, `examples`,
`deprecated`, `readOnly`, `writeOnly`, `type`, `enum`, `const`, numeric and
string/array/object bounds and assertions (`multipleOf`, `maximum`,
`exclusiveMaximum`, `minimum`, `exclusiveMinimum`, `maxLength`, `minLength`,
`pattern`, `maxItems`, `minItems`, `uniqueItems`, `maxContains`, `minContains`,
`maxProperties`, `minProperties`), `required`, `additionalProperties`,
`unevaluatedProperties`, `properties`, `patternProperties`, `propertyNames`,
`dependentRequired`, `dependentSchemas`, `allOf`, `anyOf`, `oneOf`, `not`,
`if`, `then`, `else`, `items`, `prefixItems`, and `contains`. Every schema
position is traversed recursively. Unknown keywords and external, fragment,
or unresolved references refuse projection; annotation values are opaque.
The parsed schema bundle and its digest both come from the same immutable
inventory snapshot.

`integrity.json` records `vuoro-contract-integrity/v1` and the exact POSIX relative
file -> lowercase SHA-256 inventory, excluding only itself. Include manifest,
OpenAPI, independent schemas, SDK sources and attachments. Reject extra/missing
files, links, escapes and nonregular entries; never ignore an unknown suffix.
Its digest is stored externally by the package consumer/build receipt. Always
verify inventory at load; verify the external digest when supplied. This detects
artifact drift, not semantic meaning or domain canonicalization.

The Python SDK wheel repeats just its own `operation-manifest.json`, `mcp-tools.json`,
OpenAPI document and schema bundle under `<hashed-contract-package>/data/`, with a nested
`integrity.json` covering those exact files. Its caller supplies the external
digest for that nested inventory. The runtime reads package data through
`importlib.resources`; filesystem and zip resource roots are snapshotted under
the same 16 MiB per-file, 4096-file and 64 MiB total bounds. It does not extract
data next to imported code, where ordinary bytecode could pollute the inventory.
The candidate tree's top-level inventory still covers all generated SDK and
attachment sources.

Owner source comprises main.tsp, explicit tspconfig.yaml and contract.json
(`contractVersion`, exact per-operation `exposures` and `byteBounds`, and optional
exact `mcpTools` metadata). Metadata may not define property lists or override
emitted schema/status identity. The root generator
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
schema review cannot close F1b. The generic projection engine does not create
service handlers, expose credentials, or grant permission. A runtime must still
dispatch through the existing caller authorization and owner operation paths.
Q3 upgrade/rollback/supply, F2 real owner, EC02, Q2/Q4/Q5 and OA obligations
remain separate work.

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
