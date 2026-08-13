# Packaging boundary

Vuoro has one repository and five independently installable Python
distributions. They share protocol vocabulary through versioned schemas, not
through a combined application package.

## `vuoro-client`

The client may depend on HTTP, identity-profile, cache, and JSON Schema
libraries. Its wheel contains only the `vuoro_client` Python package and the
`vuoro-client` console entrypoint. The architecture test rejects migration, adapter,
domain-core, and database-driver material in the built wheel or its dependency
metadata.

This makes client upgrades a protocol concern rather than an authority-schema
deployment. Installing the client can never grant DDL capability.

The client also validates the shared `vuoro-discovery/v1` and
`vuoro-bootstrap-manifest/v1` documents. Validation remains transport-only;
filesystem changes and package installation belong to the separate
`vuoro-bootstrap` distribution.

## `vuoro-bootstrap`

`vuoro-bootstrap` is an independently released, filesystem-owning companion.
It consumes the Cloud device-session API, refuses an unreleased compatibility
manifest, renders `.vuoro/project.json`, `.sprintctl/backend.json`, and a
complete client profile, and writes bearer credentials only with mode `0600`.
It does not own account, workspace, tenant, database, or device-authorization
state. Print-only mode must remain side-effect free.

Tag publication synchronizes and tests the complete locked workspace, builds
each distribution once, and runs release and served-conformance gates against
that wheel set. The tagged wheel is selected from that exact gated set for
publication and attestation; it is never rebuilt after the gates. GitHub
Releases are the sole artifact authority for this currently single-operator
ecosystem: the workflow attests the selected wheel, attaches it to the
verified tag's draft release, and publishes that release immediately. There
is no PyPI publication or index precedence. Consumers install an immutable
GitHub release wheel URL and verify its recorded SHA-256 digest.

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

Service-image publication uses an isolated Buildx builder and attaches a
max-level BuildKit provenance statement and an SBOM to the registry image.
It also records GitHub build provenance for the resulting digest and pushes
that attestation to the registry. A release candidate is not deployable until
the release-tag and source-commit aliases resolve to the same digest, GitHub
verifies provenance for that digest and repository, and the registry artifact
can be pulled and exercised by digest. A successful image build without those
attestations is a rejected candidate; its immutable tag and digest remain as
evidence and must not be reused.

## Shared contract wheels

`vuoro-schema-runtime` and `vuoro-adapter-kit` are independent, versioned,
stdlib-only wheels. The former provides owner-configured migration assets,
rendering, and pure read-only, fail-closed schema compatibility reports; the latter provides
strict Draft 2020-12 object schemas, operation metadata, and a structural
catalog registration protocol. They do not import the service shell, a
database driver, Pydantic, or any domain owner, and never execute SQL or DDL.
Domain repositories retain migration runners, database policy, operation
schemas, and handlers.

## Versioning

The client, bootstrap, and shared contract wheels currently use the `0.1.0`
candidate version; service releases remain independent. Releases remain
independent even when versions match. Protocol and schema compatibility are
reported explicitly by the service handshake and bootstrap manifest; equal
package versions are not a compatibility guarantee.
