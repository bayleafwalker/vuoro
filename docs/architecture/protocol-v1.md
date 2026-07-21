# Protocol v1

Protocol v1 separates transport compatibility from operation availability. A
client implements the protocol once and discovers operation-specific input and
result JSON Schemas at runtime.

## Discovery

`GET /api/meta/v1/handshake` returns:

- deployment environment name and class;
- service, API, schema, and domain versions;
- the inclusive supported client-protocol range;
- the current catalog revision; and
- overall and per-domain compatibility state.

`GET /api/catalog/v1` requires `X-Vuoro-Client-Protocol: 1`. The response uses
`operation-catalog/v1`, is sorted by globally unique operation name, and
returns the catalog revision as both body data and a strong `ETag`. A matching
`If-None-Match` returns `304`.

Every operation declares its owning domain, input and result JSON Schemas,
required authority, execution semantics, idempotency rule, deprecation
metadata, and required client schema features. Operation-name prefixes must
match their owning domain. Duplicate names cannot enter the registry.

## Schema safety and compatibility

Schemas use JSON Schema 2020-12. The first supported client feature set is:

- `json-schema-draft-2020-12`;
- `local-defs-ref`.

Every schema root must identify the JSON Schema 2020-12 dialect. Only local
`#/$defs/...` references are accepted. Dynamic and remote references are
rejected during registration. The registry detects required dialect, local
reference, and unevaluated-keyword capabilities and refuses schemas whose
features are omitted from `required_client_schema_features`. A protocol-v1
client that does not support a declared feature reports client incompatibility
before invocation; it does not mislabel the operation as unknown.

## Invocation

`POST /api/invoke/v1` takes an `invocation/v1` envelope with operation name,
arguments, client-generated request ID, catalog revision, optional basis
revision, and optional idempotency key. Actor, environment, and authorities
come from the configured identity resolver; none are accepted from the request
body. The reusable service defaults to denying every identity until a resolver
is configured.

All syntactically reachable invocation outcomes, including malformed or
extra-field request bodies, return an `invocation-result/v1` envelope. Stable
error codes distinguish incompatible clients, invalid envelopes, stale
catalogs, unknown operations, identity/environment/authority failures,
idempotency violations, bad caller input, bad adapter output, and handler
failure. Internal handler details are not returned to clients.

The request ID, optional basis revision, resolved catalog revision,
idempotency requirement, and key are passed to the owning adapter through the
invocation context. The service shell enforces whether a key is required,
optional, or forbidden; the domain adapter remains responsible for durable
deduplication semantics.

## Dynamic availability

The transport-only client caches the catalog by ETag. When an invocation names
an operation absent from its cached catalog, it refreshes discovery once. This
allows a client installed before a service release to invoke a newly registered
operation without reinstalling, provided the operation uses the client's
supported schema feature set.
