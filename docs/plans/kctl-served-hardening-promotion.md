---
doc_id: vuoro-kctl-served-hardening-promotion
status: draft
supersedes: []
---

# Kctl served-hardening promotion

This plan governs Vuoro #2045. It converts the accepted Kctl source change into
an immutable Vuoro composition update without acquiring Kctl release authority
or deployment authority.

## Evidence baseline

- Kctl `a82c6083387ca8b09b212119723067d2d4f17cbd` makes served inventory fail
  closed before SQLite access and adds the `maintain.check` and doctor
  preflight behavior.
- The source session recorded 136 passing tests and one skip. That evidence is
  useful provenance, but it is not a wheel release or a Vuoro pin.
- Vuoro currently pins Kctl `c960e8f659aa25118ac7810001f3d36aaec480e7`
  as distribution version `0.1.0`.
- Kctl #2044 remains tracker-unsettled because its dispatch manifest identifies
  the repository by alias rather than the canonical UUID required by the
  authority command. Vuoro must not manufacture or bypass that identity.

Live owner state and immutable release metadata take precedence over this
baseline if they change before execution.

## Owner sequence

1. Kctl repairs its canonical tracker identity, settles #2044 through a valid
   claim, and publishes an immutable wheel from the accepted source revision.
   These are Kctl-owned actions.
2. Vuoro verifies the release URL, downloaded SHA-256, installed distribution
   version, source revision, module, registration entrypoint, API version, and
   schema version as one identity.
3. Vuoro updates only the knowledge adapter object in
   `packages/vuoro-service/composition/adapter-pins.json` and adds any
   composition-level released-wheel smoke coverage needed to prove the
   installed artifact registers and fails closed on incompatibility.
4. An independent review checks the compatibility, migration-role,
   client-boundary, catalog-derivation, and immutable-artifact evidence before
   the source change lands.

## Acceptance

- The Kctl release record names the exact source revision and owner verification
  commands; no editable checkout or locally rebuilt wheel is substituted.
- The manifest lock and installed wheel identify the same release.
- Vuoro service tests, both package builds, the full suite, and the verification
  artifact validator pass.
- Service startup still checks compatibility and never migrates; runtime roles
  do not execute DDL.
- No Vuoro tag, OCI image, appservice digest, deployment identity, rollout, or
  cluster reconciliation is changed by this item.

## Dispatch posture

This is coordinator-only. Adapter composition and compatibility are Vuoro
`required_on_change` risk surfaces. Tests or fixtures may be delegated only
after the interface and acceptance are frozen and a registered command can
falsify an incorrect candidate.
