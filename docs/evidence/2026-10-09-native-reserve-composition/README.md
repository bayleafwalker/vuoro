# Native reserve composition source qualification

Candidate service: **0.1.88**, from Vuoro base
`0d9ce525c27db4220e3f775fdebbd1a476a68342`, for agentops#2485.
The work adapter is the actual published Sprintctl **0.14.1** wheel at source
`eaf52989a1aaf7dd0847a39ce4c2b495de1dfa96`, SHA256
`9f2b2cd760e1025eeb797037558364c5c39acd12c7012d39305e58451848aa9e`.
GitHub attestation verification binds that downloaded wheel to the source,
`v0.14.1` tag and `release-sprintctl.yaml` publishing workflow.

The manifest retains auditctl0.1.9, adapter-kit0.2.0 and schema-runtime0.1.0.
Work schema21 and compatibility floors are unchanged; no migration runs as
part of this source update. The new operation is
`work.reservation.reserve-v1`. Resource-disabled composition contains
72 work operations and five audit operations, fingerprint
`5035d2d31c516f21a5348d61db359a6160770731447d8b5dcf1b03205e8f785d`.
The work descriptor digest is
`b4b57a3e07400ce86f3d74ddf489895ac71de807b90c7b5a1d192d72489842b5`.
These fingerprints were measured from installed released artifacts, rather
than inferred from the source version or copied from earlier evidence.

## Actual checks

The targeted service package passed 317 tests, with six unconfigured PostgreSQL
cases skipped. The full source workspace passed 1600 tests with 13 unconfigured
owner cases skipped. Those source tests use the workspace's editable contract
packages; they do not establish the immutable dependency closure by themselves.

Separate actual released-artifact checks use the built service wheel plus the
four downloaded, SHA-verified composition wheels. All three work/audit/combined
catalog gates pass, all installed dependencies are consistent, and the four
built workspace wheels pass release metadata validation.

Disposable PostgreSQL16.15 checks with the released Sprintctl0.14.1 and
released kit0.2.0 passed 148 cases: 39 reserve/lease conformance, five accepted
discovery/protected-source histories, and 104 ledger/effect/provider contracts.
The isolated nonprivileged cluster is destroyed after the run. Five new reserve
HTTP-shell cases show captured envelope-key forwarding, one effect on exact
replay, unchanged activity, differing-content key conflict, refusal before any
reservation/Release for missing authority/binding/key, and distinct principal
replay namespaces. These use the actual HTTP invocation shell with its static
test identity resolver and PostgreSQL owner, not deployed credential enrollment.

Independent Astra source review approved the bounded pin and HTTP-shell tests.
An initial source-suite run used the released shared kit in the editable
workspace and failed two source-only extension tests; the source and immutable
artifact environments were separated, and both final gates pass. The first
missing-key test expected 422, but the existing shell contract returns 400;
the exact status oracle was corrected and the disposable run repeated.

## Remaining release and deployment gates

This record qualifies source and local artifacts. Exact-head CI, service wheel
and OCI publication, matching tag/source aliases, provenance verification,
pull/exercise by image digest, installed composition checks and an authorized
consumer repin remain separate gates. Startup must continue to check rather
than migrate. A compatible prior service digest is the deployment rollback;
native owner history must be preserved.

A caller must independently possess a stable principal/workspace binding and
`work:write` for native reserve. A profile's requested authorities and an actor
display name do not establish that binding. This source grants no authority,
changes no deployed identity, and does not qualify the full S4 causal pipeline,
historical import, hook cutover or soak.
