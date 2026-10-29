# Candidate reference-distribution contract

Status: **source contract only**. `deploy/reference-distribution.toml` binds the
current composition pins to their advertised API/schema versions, records each
capability's maturity, and describes lifecycle and support boundaries. It does
not declare a supported release, deployment, independent-user result, or
qualification. The product-delivery plan makes this product work in scope;
the evidence gates remain open.

## Composition and maturity

The composition manifest is the source of owner artifact URLs, immutable
source revisions, digests, and distribution versions. The candidate contract
repeats each runtime descriptor's `lock_id`, distribution/version, and
API/schema labels alongside its capability and maturity. A test fails if a
descriptor or pin changes without reconciling that contract. The service
package version is checked against its project metadata. This consistency
check prevents stale declarations; it does not prove that a release is
installed or deployed.

Maturity is reported as separate facts:

| State | What this source records |
| --- | --- |
| Pin maturity | An immutable released owner artifact and source revision are named in the checked-in composition manifest and mirrored by lock ID/version in the candidate contract. |
| Composition maturity | Source composition and automated tests are present. |
| Deployment maturity | Unknown here; a source pin or green CI is not rollout evidence. |
| User qualification | Not qualified until EE-02, EE-03, EE-10 and EE-11 evidence exists for the same release. |

The three current runtime domains are work, audit and knowledge. Their API and
schema labels are owner-facing compatibility labels, not independent guarantees
about every behavior a user might expect. The pinned version is exposed for
inspection but remains subordinate to the immutable artifact URL and digest.

## Workflow package and support matrix

The manifest now records a candidate `repository-delivery` package owned by
AgentOps and a hybrid support-matrix entry tied to the exact composed runtime
locks. Its released project adapter and entrypoint have yet to be selected;
execution, verification and gateway requirements are explicitly unsupported by
this composition. The package describes requirements and evidence; it does not
create a grant or override work, execution, acceptance or resource-owner policy.
The managed workbench and clean acceptance lab are target environment profiles,
not provisioned environments.

The matrix records unknown runtime, OS, database, gateway and storage details and
open qualification references. It does not assert support. Run
`python scripts/validate_workflow_distribution.py` to check package references,
composition lock coverage, lifecycle links and source-only status. This is a
consistency diagnostic, not an installer, a compatibility check or qualification.
A future qualification contract must explicitly define evidence and validation
before changing these fail-closed `not-qualified` records.

## Lifecycle boundary

The TOML contract gives each lifecycle operation a source status, procedure,
qualification state and explicit limit:

- **Install:** the Compose stack is for disposable local evaluation. The
  Kustomize base is neutral and incomplete until an installation supplies an
  immutable image digest, environment configuration, identity and role DSNs.
  Neither is a qualified self-service install.
- **Upgrade and migration:** the service checks schema compatibility at
  startup and does not run DDL. Owner migration jobs remain explicit and
  suspended until the installation supplies migration-role credentials and
  obtains each owner's evidence.
- **Rollback:** an old image is usable only while it admits the current schema.
  After an incompatible schema commit, recovery is a compatible forward fix or
  coordinated restore, not an image-only rollback.
- **Backup and restore:** the application contract does not own infrastructure
  backup cadence, object retention, recovery-only credentials or a restore
  target. Those remain installation duties and require EE-03 evidence.
- **Export and import:** Workspace's contract covers its own data. That does
  not yet prove a complete export/import of the service plus every external
  owner's records; EE-10 and EE-11 remain gates.
- **Diagnostics:** `vuoro-ops` exposes structured read-only checks. A source
  checkout without the installed composition attestation reports that absence
  as unknown and cannot establish live state.
- **Removal:** a conservative candidate procedure is recorded in
  `docs/operations/reference-distribution-removal.md`. It inventories neutral
  and live resources, stops delivery before compute removal, requires owner
  disposition for every data store, retains data by default, and checks for
  remaining or orphaned runtime resources. It authorizes no purge and has not
  been independently exercised or qualified.

## Version relationship and compatibility policy

The candidate release unit is one immutable Vuoro service artifact together
with the exact adapter artifacts, revisions and digests in its composition
manifest. Adapter pins are reviewed and qualified as a composition; installing
an individual adapter's latest release is not an upgrade procedure. Domain
owners remain responsible for schema versions and migrations. Migrations stay
explicit and owner-run, and service startup must reject unsupported schema
compatibility without applying DDL.

There is no committed upgrade grace period or public support promise. If the
new service and current schema are incompatible, freeze delivery while the
schema owner provides a compatible forward upgrade or the installation
executes its coordinated restore plan. Roll back the image alone only while
the previous image accepts the current schema. A source compatibility check
or pinned version relation does not establish runtime qualification; the
candidate policy and open qualification are recorded in
`deploy/reference-distribution.toml`.

## Support and product claims

Vuoro owns its service release artifacts and neutral contracts. `vuoro-cloud`
owns hosted tenant lifecycle and its operator surface. Sprintctl, Auditctl and
Workspace own their schemas and migration semantics. The installation
operator owns infrastructure access, backup and recovery. This ownership map
does not assign a support responder, SLA or restoration commitment; none is
currently committed by this contract.

The candidate is **not a supported distribution**. Do not describe it as
independently installable, production-ready, fully portable, or supported. The
distribution claim becomes eligible only after the same released composition
passes installation, upgrade/migration, rollback, export, restore, diagnostics,
removal and support qualification with independent users and a recorded cost
and burden decision. A passing source test is one input to that evidence, not a
substitute for it.
