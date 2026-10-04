# vuoro-adapter-kit

`vuoro-adapter-kit` contains pure, dependency-free builders shared by Vuoro
domain adapters: strict Draft 2020-12 `object_schema` construction and
validated `operation_spec` dictionaries. Inputs are deep-copied so later
owner-side mutation cannot change a published spec. The optional
`CatalogRegistry` is typing-only; registration and handlers remain in each
owner/service integration. The package does not depend on Vuoro service,
Pydantic, JSON Schema validators, or any domain package.

The explicit `operation_spec_v2` builder emits `operation-definition/v2` metadata
for `catalog-required/v2`: a sorted AND set of required authorities, explicit
`released-legacy` or `strict-only` placement, and optional versioned owner admission
contract for required-key, repository-scoped strict mutations. It leaves all v1
builder output unchanged. Inner schema array order remains authored order.

The admission module supplies frozen, deeply immutable identity/context/request/
response values and a structural `OwnerAdmission` protocol. Exact argument and
response bytes are never parsed or normalized here. Argument byte framing is
bounded at 65,536 bytes; owner parsing, digest classification, capability evaluation,
quota/decision transactions and semantic validation remain outside the kit.
`TrustedIngressProvenance` has a closed `resource-review-ingress/v1` shape bound to
the same end principal; this internal profile is not a new signed-assertion format.

These data types do **not** authenticate callers, mint principal epochs, verify a
trusted ingress or confer write authority. The shell constructs the context after
actual authentication and guard checks; the owner rechecks admission and its role
boundary. Mutable collections and wire string/bool/mapping substitutes are
rejected structurally. Constructing a Python value is not a security boundary
against arbitrary in-process code. No resource implementation, reviewer transport,
byte verifier, schema migration, capability grant or deployment is supplied here.
