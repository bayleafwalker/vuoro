# Discovery and bootstrap release contract

Status: release-contract candidate. The public control API and device
authorization remain owned by `vuoro-cloud`; this repository owns the shared
payload vocabulary, transport validation, and the separate `vuoro-bootstrap`
filesystem boundary.

## Contract boundary

`/.well-known/vuoro` returns `vuoro-discovery/v1`. Its bootstrap and manifest
URLs must use the declared API origin, its normalized API endpoint must exactly
match the endpoint requested by the bootstrap client, and it must advertise
protocol v1. The manifest returns `vuoro-bootstrap-manifest/v1` and is
consumable only when:

- `release_ready` is true;
- `vuoro-bootstrap`, `vuoro-client`, and `sprintctl` are immutable released
  versions, not `UNRELEASED`, `LATEST`, `HEAD`, or `MAIN`;
- the service protocol range includes v1.

The client validates these documents without adding filesystem or package
manager behavior. `vuoro-bootstrap` performs the device-session HTTP calls and
renders local changes. It refuses conflicting existing files and writes the
credential with mode `0600`. A print-only plan is pure and does not mutate the
repository.

The session response must return the discovery document's normalized
activation URI and positive integer expiry and polling intervals. Exchange is
performed below the declared bootstrap-session collection, with the opaque
session identifier encoded as one path segment. Existing session and credential
files are accepted only with exact mode `0600`, including on idempotent writes.

Cloud may add fields to a later schema version, but a v1 response is strict:
unknown, cross-origin, non-HTTPS, and contradictory fields fail closed.

## Release identity

The service handshake includes the installed `vuoro-service` distribution and
version. It is package identity only; an OCI digest, gateway deployment, and
tenant routing remain deployment-owned evidence. The release workflow publishes
each Python distribution from its own immutable `vuoro-*-v<version>` tag and
attests the resulting wheel. A tag must match the wheel's normalized metadata.
The published wheel is selected from the exact wheel set that passed the full
workspace suite and served-conformance gate; publication never rebuilds it.

## Served conformance

`scripts/validate_served_conformance.py` installs all three built Python
wheels in a disposable environment and exercises HTTP handshake, ETag catalog,
accepted invocation, stale catalog, malformed envelope, unsupported schema,
authority, and repository rejection paths. It uses only an in-memory adapter;
it does not open a database, run migrations, import an owner repository, or
contact a deployed endpoint. The Cloud-shaped discovery, manifest, and profile
fixtures are checked separately in `tests/test_served_conformance.py`.

The fixture proves the shared contract. It is not evidence that the public
Cloud deployment, gateway, tenant runtime, or external Sprintctl doctor is
available; those checks remain in their owning repositories.
