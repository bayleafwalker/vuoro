# Served project composition boundary

Status: release composition prepared. The checked-in composition pins the
canonical Agentops project binding at an immutable source revision and enables
the released Sprintctl project aggregates after normal release verification.

`project.toml` remains canonical in the project home repository. Vuoro must
not discover a caller's checkout, derived project folder, or local
`project.context.json`: those are mutable client inputs and cannot safely
choose the repositories a served request reads.

## Immutable input

Before a work-adapter release adds `work.project.context` or
`work.project.sprints`, the Vuoro release composition must contain one
reviewed `vuoro-project-bindings/v1` projection. Each binding records:

- the canonical `project_id`, home repo, and ordered member repo IDs;
- the canonical source repository, full commit SHA, source path, and SHA-256
  of the source `project.toml`.

The parser in `vuoro_service.project_binding` rejects malformed IDs, duplicate
members, a home repo outside the members, path traversal, and non-immutable
source provenance. The projection does not create a project database or make
Vuoro authoritative for project membership; it is release-reviewed data used
only to construct domain applications.

The source projection must be incorporated into the immutable service
composition at image-build time. Do not add an environment variable, mounted
secret, ConfigMap, or local file lookup for it: deployment overlays must not be
able to change catalog behavior or widen the repositories an aggregate reads.

## Construction and authorization

Composition must build a distinct Sprintctl `WorkApplication.postgres` for
every declared member, with a `PgStore` scoped to that member's `repo_id`, and
pass the ordered members to Sprintctl's `ProjectWorkApplication`. It must not
reuse a request-rescoping single-repo application for an aggregate.

Project operations have no one envelope `repo_id`, so the ordinary service
repository gate cannot protect them. Wrap each owner project application in
`AuthorizedProjectApplication`. It rejects the request before any member call
unless the `work:project-read` identity authorizes *every* declared member.
This is intentionally all-or-nothing: partial results disclose project
membership and can mislead a blind agent about available work.

The component raises `ProjectAuthorizationError`; the Sprintctl adapter
integration must translate that into its normal `project-repo-unauthorized`
structured rejection. Keeping the component independent of an editable domain
checkout is deliberate: Vuoro tests must exercise the release composition,
not accidentally import a sibling source tree.

## Release implementation

The composition contains exactly one reviewed binding and creates a distinct
repo-scoped Sprintctl application for each declared member. The released work
adapter owns `work.project.context` and `work.project.sprints`; Vuoro supplies
the guarded application and immutable provenance only. Adding another binding
requires an owner contract with an explicit project selector and a new release.
