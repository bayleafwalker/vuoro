# Acceptance explanation release candidate

This release candidate packages the reviewed caller-bound acceptance transport
as MCP edge 0.1.7 inside service 0.1.94. It follows the ordinary service wheel
and image release workflows after the source lands. MCP edge has no separate
wheel release workflow. No release artifact or deployment is claimed by this
plan or by changing package versions.

The projection remains the published operator-projection 0.2.5 wheel and exact
SHA-256 from the transport contract. Client 0.1.2, Sprintctl 0.18.0, auditctl
0.1.9, adapter-kit 0.2.0 and schema-runtime 0.1.0 release pins stay unchanged.
Versioned service identity expectations change with the service version;
owner APIs, schema compatibility and migration assets do not change.

The exact release commit must pass wheel identity/boundary gates, workspace
tests, targeted caller transport qualification, production image dependency
and composition checks, installed projection provenance and byte comparison,
declared-change preview, strict MCP and workspace isolation checks. Actual
published wheel and image bytes, source provenance and tag/source alias digest
equality must be verified after release; local build success is insufficient.

Cloud rollout stays a deployment-owner operation. An existing 0.1.93 rollout
candidate can be revised to the actual published 0.1.94 digest only with a new
reviewed contract and paired manifest/Secret pins, repeated disposable old
composition upgrade qualification, controller hold/resume stages, per-tenant
backup and writer fencing, exact migration ownership and post-migration owner
readback. Real Claude/ChatGPT and refreshed grant checks remain separate gates.
