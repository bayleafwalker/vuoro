---
doc_id: vuoro-service-0.1.44-promotion
status: final
recorded_at: 2026-08-13
---

# Vuoro service 0.1.44 release and promotion record

This is the durable Vuoro-side evidence record for the 0.1.44 service
release. Appservice retains deployment authority and its Git history remains
the source of desired-state truth.

## Released artifact

- Source tag: `vuoro-service-v0.1.44` at `a7c6544`.
- OCI artifact:
  `ghcr.io/bayleafwalker/vuoro-service@sha256:f5b382a8abe90640d6bfebe720641251407dc563b1e52ee93c85e3710ccdd5c3`.
- The release tag and source-commit aliases resolved to that same digest.
  GitHub provenance was verified for the Vuoro repository and the candidate
  was exercised by digest before deployment.
- GitHub Releases are the sole publication authority for Vuoro Python wheels
  and shared packages in this single-operator ecosystem. No PyPI publication
  or index fallback is used.

`vuoro-service-v0.1.43` is superseded. Its Python wheel publication completed,
but its OCI image build failed while installing the shared adapter wheel. It
never became an accepted deployable candidate and must not be promoted or
reused.

## Dev canary

The 0.1.44 digest was first deployed through appservice GitOps to `vuoro-dev`.
The initial canary identified a missing observer-policy mount; appservice PR
#1474 added that prerequisite without changing Vuoro's catalog, domain
schemas, or authority model. The corrected canary then reported:

- Flux Ready and one ready service replica;
- compatible readiness and a 0.1.44 service/release handshake;
- all work, execution, knowledge, and audit domains compatible;
- an 80-operation catalog; and
- accepted and resolved routes.

## Production/shared promotion

After the dev result, appservice PR #1475 promoted the same attested digest
to `vuoro-shared`. It updated both `vuoro-service` and `actionq-db-proxy`,
then Flux reported revision `906f763d` Ready. The shared pod was 2/2 Ready
with zero restarts; its readiness, four-domain compatibility, 80-operation
catalog, 0.1.44 handshake, and route acceptance/resolution matched the canary.

This promotion made no database, schema migration, secret, or route changes.
Rollback remains a normal appservice Git revert/new desired-state commit that
restores the prior paired image digests; it is not a `kubectl` patch.
