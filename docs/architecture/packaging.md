# Packaging boundary

Vuoro has one repository and two independently installable Python
distributions. They share protocol vocabulary through versioned schemas, not
through a combined application package.

## `vuoro-client`

The client may depend on HTTP, identity-profile, cache, and JSON Schema
libraries. Its wheel contains only the `vuoro_client` Python package and the
`vuoro` console entrypoint. The architecture test rejects migration, adapter,
domain-core, and database-driver material in the built wheel or its dependency
metadata.

This makes client upgrades a protocol concern rather than an authority-schema
deployment. Installing the client can never grant DDL capability.

## `vuoro-service`

The service owns FastAPI/uvicorn hosting and separate process entrypoints for
serving, compatibility checks, migrations, and authorized administration.
Importing or starting the service does not run migrations. Domain adapters and
their migration entrypoints will be consumed as pinned releases from the four
owner repositories.

The released service image contains the checked-in composition manifest and
the four downloaded adapter wheels. Each wheel is checked against the
manifest's SHA-256 before it is installed; the running process checks the same
bundled wheels before importing an adapter. Deployment must pin the resulting
OCI image digest. Configuration may provide runtime DSNs and an identity
registry, but cannot change the catalog composition.

## Versioning

The two distributions start at the same development version for repository
bootstrap, but releases are independent. Protocol and schema compatibility are
reported explicitly by the service handshake; equal package versions are not a
compatibility guarantee.
