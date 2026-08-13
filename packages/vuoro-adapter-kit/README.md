# vuoro-adapter-kit

`vuoro-adapter-kit` contains pure, dependency-free builders shared by Vuoro
domain adapters: strict Draft 2020-12 `object_schema` construction and
validated `operation_spec` dictionaries. Inputs are deep-copied so later
owner-side mutation cannot change a published spec. The optional
`CatalogRegistry` is typing-only; registration and handlers remain in each
owner/service integration. The package does not depend on Vuoro service,
Pydantic, JSON Schema validators, or any domain package.
