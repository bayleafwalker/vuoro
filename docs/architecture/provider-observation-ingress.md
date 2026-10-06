# Provider observation ingress: first MI-1/P2 increment

Source increment for served agentops item 2613 (TS-18), 2026-10-06. The pure
`vuoro_evidence.ingress.provider` functions normalize supplied provider events
and prepare fields for the existing `work.evidence.append-v1` owner. They add no
store, runtime operation, transport, webhook endpoint, grader or automatic
Decision. They are not self-registered into the historical HostProto decoder.
Removing this module leaves core work identity, evidence history and authority
unchanged.

## Primary schemas checked before implementation

- [Anthropic outcome event schema](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/beta/sessions/beta_managed_agents_span_outcome_evaluation_end_event.py)
  and [Managed Agents event reference](https://platform.claude.com/docs/en/api/beta/sessions/events),
  read 2026-10-06: the end event carries an event/outcome reference, verdict and
  explanation. An outcome ID is not a session ID, rubric revision or work ID.
- [GitHub webhook reference](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
  and [check-run schema](https://github.com/octokit/webhooks/blob/main/payload-schemas/api.github.com/check_run/completed.schema.json)
  / [workflow-run schema](https://github.com/octokit/webhooks/blob/main/payload-schemas/api.github.com/common/workflow-run.schema.json),
  read 2026-10-06: checks/runs report a commit reference and conclusion. These
  fields establish neither exact resulting-artifact bytes nor a check-definition
  revision. PR actions are lifecycle observations; even a merged PR is not a
  protected verifier result.

Supported inputs are the Managed Agents `span.outcome_evaluation_end` event and
GitHub `check_run`, `workflow_run` and `pull_request` payloads. This decoder is
not a complete provider JSON Schema validator. It validates the fields it uses,
retains unfamiliar verdict strings as observations and rejects unsupported
provider/event families. Primary schemas must be rechecked when expanding it.

## Evidence and unknown fields

`normalize(provider, event, payload, delivery_id=..., observed=...)` requires a
parsed payload object and an external delivery identity. For GitHub, that is the
collector's captured delivery header; payload object IDs can recur across
updates and must not substitute for it. Its namespaced retry key binds provider,
repository, event family and delivery ID, independently of content or verdict.
The payload digest binds canonical parsed JSON, **not original HTTP bytes**.
The caller retains the original bytes at the supplied reference; byte integrity
or signature verification is outside this decoder.

Provider/build, session reference, artifact digest, rubric/check revision,
observed instruction digest and assurance are explicit. Unprovided fields are
`unknown`; the provider identity alone is known from the selected decoder.
Optional collector observations occupy separately named fields, list which
fields were supplied and stay unverified. A commit SHA cannot masquerade as an
artifact digest. Typed artifact/instruction digests use `sha256:<64 lowercase hex>`.
Assurance is fixed at `unverified-supplied-payload`; arbitrary payload extensions
and caller metadata cannot elevate it.

`evidence_item_draft(observation, ref=..., collected_at=...)` binds the normalized
record digest, original-payload reference and a zoned collection time. Every
claim is an `observation` with null grant and null confirmation. No accepted
result, effect completion, grant, identity, policy Decision or attestation is
minted. The observation remains historical at its digest even when it names a
stale artifact. Artifact-match evaluation belongs to the protected verifier.

## Owner integration and acceptance boundary

The draft is **not a complete append request**. A native authorized producer
must resolve its own registered run, bind its actual principal/workspace/grant,
read the owner's chain tail, retain the immutable request and synchronize it
through Sprintctl's existing native evidence intake. The owner enforces durable
idempotency and refuses conflicting replay. The provider session reference
never supplies the owner run ID. This module neither invokes append nor creates
a second producer outbox. Any retry of one delivery must reuse the original
capture and collection time, not recollect a different request under its key.

Same-delivery identical normalization yields the same draft. Changed verdict or
artifact under that delivery preserves the key while changing the bound digest;
it is a conflicting request, not silently replaced evidence. Different delivery
identities remain distinct even when provider object IDs recur.

Targeted tests exercise missing fields, stale/mismatched artifact retention,
no acceptance from provider success, no caller assurance lift, replay-key/content
separation and the unchanged core boundary. The existing generic rerun path
returns `reacquire` for a successful provider observation on a wrong artifact;
it does not authorize effects. This is not full P2 acceptance: durable
owner-ingestion/replay histories, a real captured provider case and the neutral
P3 proof still need to be bound through the actual append owner. No deployment,
published package or hosted reachability is claimed by this source increment.

## Supplied GitHub REST check responses

`normalize_github_check_response(payload, repository=..., capture_id=...)`
handles a captured [GET check-run response](https://docs.github.com/en/rest/checks/runs#get-a-check-run)
(primary source rechecked 2026-10-06). It labels the record
`check_run_response` and `supplied-rest-get-response`, with repository scope from
the captured request. It does not manufacture a webhook delivery or signature
verification. A capture ID names one immutable response capture; the recurring
check object ID cannot serve as its retry key. The canonical payload digest
binds the original response object, without a synthetic webhook wrapper.
Missing build, artifact and check-definition digests remain unknown. The result
still carries unverified supplied-payload assurance; a collector's actual
transport observation must be recorded separately and cannot turn this decoder
into an attestor or protected verifier.
