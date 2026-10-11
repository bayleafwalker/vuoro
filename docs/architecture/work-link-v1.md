# Vuoro work link v1

`vuoro-work-link/v1` is a portable locator for one work item and one
predecessor run. It carries identifiers and, optionally, the seven-field owner
checkpoint used by `work.run.continuation-delta-v1`. Its JSON contract is
[`work-link-v1.schema.json`](work-link-v1.schema.json).

The canonical copyable URI is
`vuoro-work://link/v1/<payload>`. `<payload>` is the unpadded base64url
encoding of the link object serialized as canonical JSON: UTF-8, object keys
sorted by Unicode code point, no insignificant whitespace, and standard JSON
escaping. The complete URI is ASCII and at most 4096 bytes. A reader accepts
only this exact scheme, authority, path shape and canonical payload. It rejects
padding, query strings, fragments, duplicate JSON keys, noncanonical JSON,
invalid UTF-8, schema violations, and checkpoint `repo_id` or `item_id` values
that differ from the outer locator.

This custom URI is text to copy into a Vuoro work-link reader. It is not an
HTTP URL: clients must not resolve it through DNS, fetch it, or open it as a
network resource. Its identifiers are not credentials. Possession grants no
workspace, repository, owner, run, or item authority. The link contains no
principal, grant, token, client credential, lease, transcript, decision bytes,
or owner response. It contains no claim about currentness or authorization.

## Owner-bound handoff

The maker operates under its current authenticated identity and an explicitly
selected repository. It obtains the selected work item through the existing
`read_work_delta` owner read, naming its own owned run and the item explicitly.
Before creating a link, it checks the owner envelope's top-level `repo_id` and
the exact delta schema. It also checks `authorization_scope.run_id` equals its
own run, `authorization_scope.repo_id` equals the selected repository, and
`run_owned` is true. It checks `current.repo_id` and `current.item_id` match the
selected repository and item, and `work.item_id` matches the item. Only then
does it copy the complete seven-field `current` object into the link. The
checkpoint is comparison input only; it does not make owner facts current and
does not authorize an action. A reader without a checkpoint makes the next
owner comparison unknown rather than inventing a baseline.

The reader uses its own current identity and selected repository. It refuses a
link whose workspace or repository differs from that identity before any
owner read. The reader agent registers its own successor run separately,
naming the link's `predecessor_run_id` through the existing
`register_run` contract and its existing `work:evidence` plus `work:read`
requirements. The read-only link reader then calls the existing
continuation-delta owner read with that successor run, the exact linked item,
and the optional checkpoint. It accepts the unchanged owner response only
when top-level `repo_id`, `authorization_scope.repo_id`, `current.repo_id`,
`current.item_id`, and `work.item_id` match the link;
`authorization_scope.run_id` matches the successor run and `run_owned` is
true; and `predecessor_run_id` matches the link. An owner refusal remains a
refusal; the reader does not synthesize a delta or write owner state.

The link does not transfer authority. Evidence registration, work claims,
settlement, and every other mutation remain bound to the reader's own run and
existing owner guards. The Native work-link contract adds no scope, grant,
principal, owner operation, or owner database. A later Cloud exposure, if
qualified, is limited to the existing `vuoro:work.read` surface for the
read-only link tools; successor registration remains a separate existing
operation under its existing authorities.

## Native tool surface

The Native edge exposes `make_work_link` and `read_work_link` only when its
owner catalog advertises `work.run.continuation-delta-v1` and the run registry
supports predecessor continuation. Both tools use the existing `work:read`
authority and this same owner operation. They add no owner operation or state;
Cloud exposure is a separate allowlist change and is not implied here.
