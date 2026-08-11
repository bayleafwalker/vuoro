# Served project composition boundary

Status: release composition prepared. The checked-in composition pins the
canonical Agentops project binding at an immutable source revision and enables
the released Sprintctl project aggregates after normal release verification.

`project.toml` remains canonical in the project home repository. Vuoro must
not discover a caller's checkout, derived project folder, or local
`project.context.json`: those are mutable client inputs and cannot safely
choose the repositories a served request reads.

## Binding inputs

Before a work-adapter release adds `work.project.context` or
`work.project.sprints`, Vuoro accepts one of two explicit
`vuoro-project-bindings/v1` inputs. The checked-in release input uses
`bindings` and records:

- the canonical `project_id`, home repo, and ordered member repo IDs;
- the canonical source repository, full commit SHA, source path, and SHA-256
  of the source `project.toml`.

The parser in `vuoro_service.project_binding` rejects malformed IDs, duplicate
members, a home repo outside the members, path traversal, and invalid
provenance. The projection does not create a project database or make Vuoro
authoritative for project membership; it is used only to construct domain
applications.

Vuoro Cloud owns the runtime-generated form. It emits `environment` and one or
more `projects`, with each project carrying its hosted ULID, descriptor digest,
and ordered repository records (`repo_id`, `git_remote`, `commit_sha`). Cloud
generates and deploys exactly one such project as a read-only ConfigMap mount;
Vuoro parses it, requires exactly one project at startup, and retains the
repository provenance while applying its normal repository and authority
checks. At startup, Vuoro requires the hosted `environment` to exactly match
the appservice-controlled `VUORO_ENVIRONMENT_NAME`; disagreement or a missing
expected environment fails closed. Vuoro does not absorb Cloud's workspace
state or deployment policy.

The deployment-trust contract is deliberately narrow: the embedded default is
`/opt/vuoro/composition/project-bindings.json`; the only runtime override is
the exact ConfigMap path `/etc/vuoro/bindings/bindings.json`. The override must
be a read-only Cloud-owned mount, and ConfigMap `..data` symlinks are allowed
only when their resolved target remains under `/etc/vuoro`. Relative paths,
other absolute paths, broken/out-of-root links, malformed or undecodable files,
and empty or multi-project documents fail closed. The path selector is not a
general deployment-controlled authority and does not make arbitrary mounted
JSON release-reviewed.

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
