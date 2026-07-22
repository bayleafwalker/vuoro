# Neutral Kustomize base

This base deliberately contains no usable endpoint, credential, identity, or
image digest. Appservice overlays must replace the all-zero digest with the
immutable release candidate digest, supply `vuoro-runtime-dsns`,
`vuoro-migration-dsns`, migration/runtime role secrets, and the mounted
`vuoro-identities` registry.

The four migration Jobs are intentionally `suspend: true`. An overlay enables
each job only with its migration-role DSN, waits for owner compatibility
evidence, then deploys the runtime role. The deployment never receives a
migration DSN, and no job should be enabled automatically as part of a service
rollout.

The base has no namespace, CNPG resource, ingress, backup policy, or concrete
network destination. Those are deployment-owned appservice concerns.
