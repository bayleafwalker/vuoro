# MI-1 served Release contract composition (2026-10-09)

Service 0.1.87 consumes Sprintctl 0.13.2, source
`050a8181101473f312174871858f48e9c9f885c9`. The published wheel SHA256 is
`450b67a54c03c5de1383a2ce3f7cc428f17160eb88f0f77c63a7056bc192c705`.
GitHub attestation verification binds that wheel to `refs/tags/v0.13.2`, the
reviewed source and `release-sprintctl.yaml` (release run 37891113657).

The owner now accepts the optional `acceptance_contract` object through
`work.reservation.reserve`. A supplied contract requires an exact item basis
and an execution reservation. The owner freezes it in the Release using its
existing transaction; this service adds no policy or domain authority.
`effect_verification_required: true` can therefore be required through the
served API before an actual protected artifact proposal. Owner source and
release are Sprintctl PR130; optional contract metadata changes the catalog
fingerprint while retaining 71 work and five audit operations.

Schema21, work API/schema identifiers, audit0.1.9, adapter-kit0.2.0 and
schema-runtime0.1.0 remain compatible. No migration is part of this release.
The historical October6 verification packets retain their original artifacts.

Validation: service tests 317 passed/one unconfigured PostgreSQL skip; full
workspace 1600 passed/eight unconfigured PostgreSQL skips. Installed published
owner/shared wheels pass dependency consistency and work/audit/combined
catalog gates. Four protected verification/recovery histories pass against
the published0.13.2 owner on disposable PostgreSQL16.15. Wheel release
identities, authored knowledge and verification artifacts validate.

This is a source composition increment. Deployment convergence, actual
cross-harness protected acceptance/application and MI-1 parent acceptance
require their separate live evidence; none is implied by these tests.
