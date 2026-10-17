# Candidate reference-distribution removal procedure

Status: **source procedure only; not independently exercised or qualified**.
This runbook defines a conservative operator sequence for removing the neutral
Vuoro composition. It does not authorize removal from any live installation,
decide ownership of data, or provide a purge command.

## Before stopping anything

1. Name the installation owner and every external owner whose database,
   object store, identity, or other resource is in use. Record the exact service
   image digest, composition manifest, overlay, namespace/project, and the
   operator who can restore them. If an owner or resource boundary is unknown,
   stop here.
2. Freeze new delivery to the installation. Disable its ingress or caller
   routing and scheduled/event-driven producers using the installation's own
   controls. Confirm that new requests cannot reach the service. The neutral
   distribution does not own those controls.
3. Inspect the installation's runtime and lifecycle status using its own
   authenticated operator path. Drain or stop service workloads and explicit
   migration jobs. Do not infer quiescence from a source checkout or an
   off-image diagnostic. If a job cannot be confirmed stopped, stop here.

## Preserve and assign data

4. Inventory resources declared by the exact neutral Compose project and
   Kustomize overlay, then compare them with the live installation. Include
   service workloads, suspended migration jobs, database and object-store
   references, secrets/identity bindings, ingress, persistent volumes,
   backups, and resources that may have been created outside those manifests.
   Record owner, identifier, location, retention rule, and current state for
   each. Investigate every unrecognized or orphaned resource before continuing.
5. Before removing service compute, make the owner-approved exports and
   backups required by the installation's retention policy. Record their
   destination, encryption/access controls, retention expiry, and the exact
   restore procedure and target. Verify a restore using the relevant owner
   procedure when required. Vuoro's Workspace export is not a whole-system
   export; it does not cover external owner records. If the required export,
   restore target, or owner approval is unavailable, stop with delivery
   disabled and resources/data retained.
6. Obtain an explicit disposition from each data owner for each database,
   object store, volume, backup, and audit/evidence record: retain in place,
   transfer to a named successor, or delete under that owner's policy. Silence
   is not consent to delete. No step in this runbook defaults to purge, volume
   deletion, object-store emptying, or backup expiry changes. Record the
   authorizing owner and retention period before any separately authorized
   deletion.
7. Revoke or transfer service credentials, identity bindings, and operator
   access according to the owner of each credential. Verify revocation with
   that owner; removing a Kubernetes secret or local environment file alone
   does not prove a remote grant was revoked. Keep only the recovery access
   that the named data owner requires to retain or transfer data.

## Remove compute and verify

8. After delivery is stopped, workloads and migration jobs are confirmed
   stopped, preservation checks are complete, and credential disposition is
   recorded, remove only the service-owned compute and routing resources
   identified in step 4. Use the installation's exact overlay/project and
   review the rendered/deletion target before applying it. Do not use broad
   namespace, project, volume, or object-store deletion commands.
9. Re-inventory runtime resources independently. Confirm no service workload,
   migration job, ingress route, or scheduled producer remains. Reconcile
   every originally inventoried resource to one of: intentionally retained,
   transferred, or separately authorized deletion. Investigate and retain any
   unexplained resource; report it as an unresolved orphan rather than
   claiming complete removal.
10. Preserve the removal record, exact artifacts and overlays, owner
    dispositions, export/restore evidence, credential revocation results, and
    post-removal inventory with the installation owner. An independent
    reconstruction test is still required before claiming this candidate is
    portable or supported.

## Qualification boundary

This procedure supplies a source sequence, not proof that a reference
installation can be removed or reconstructed. No independent-user exercise,
whole-system export/restore, orphan-free removal, support response, or cost and
burden result is asserted. Those qualification states remain open in
`deploy/reference-distribution.toml`. In particular, do not convert these
steps into an automated uninstall or add destructive defaults without a
separate owner-approved policy and tested recovery path.
