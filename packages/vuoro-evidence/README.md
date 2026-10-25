# vuoro-evidence

The EvidenceSet consumer built for the HostProto validation phase
(hostproto-semantics `docs/EVIDENCESET_VALIDATION.md`, ADR-0013).

- `core/` — `EvidenceSet`, `EffectGrant`, `Decision`, the claim vocabulary,
  the reducer and the rerun decision path. Host-agnostic; the boundary test
  fails if any profile, adapter or host vocabulary appears here.
- `ingress/` — registered decoders. `hostproto` turns receipts, errors,
  observations and evidence refs into claims. The `command-capture` decoder was
  removed on 2026-08-29 when outctl was retired; see `RECOVERY.md`.

`EffectGrant` is input from ActionQ/federation. Grant use is a projection
of effect state in the reducer; no ingress edge can assert it.

MI-1/P2 adds a pure, explicit provider observation decoder and append-owner draft
builder in `ingress/provider.py`. It does not register a runtime capability,
automatically ingest, verify a signature or convert verdicts to Decisions. See
[the current increment and acceptance boundary](../../docs/architecture/provider-observation-ingress.md).

## Provider-owned delivery workflow bindings

`vuoro_evidence.workflow` records an immutable join between a provider-owned
workflow binding and an existing work-owner release. Sovite authors and validates
the delivery definition, resolves installed roles and grants, and owns current
admission. The work owner retains release identity and acceptance. This package
performs structural validation, digest checks and comparison with caller-supplied
expectations; it has no scheduler, instruction compiler, policy engine or network
client.

The supported provider envelope is `sovite.dev/v1alpha1` /
`DeliveryWorkflowBinding`. It carries the full provider definition and content
revision, installation ID, exact bundle generation, input class, resolved roles,
transfer bindings with pinned output-contract revisions, and the requirements
and session-policy references. Both references are required;
an unbound definition preview cannot be mistaken for a delivery binding.
`dispatch_authorized` must be `false`: binding a configuration is not an admission
or authorization decision. Public and private model destinations are both opaque
provider-owned IDs here. Boundary classifications, class identifiers and release
requirements are preserved as supplied by Sovite; Vuoro checks their envelope
types without deciding whether a transfer is permitted or an output was released.
The same record supports provider-bound subscription sessions (Codex and Claude),
OpenCode Go routes, API models and private models. Provider/account selection and
native authentication remain Sovite responsibilities; recording a model ID or
adapter never establishes a verified weights identity or grants runtime access.
An offline compiled binding can be recorded as a proposed selection. Execution
also requires Sovite to record the exact binding through its owner authoring
interface and admit each job under current policy. Exporting a Vuoro join creates
neither authority.

`RevisionRef` is the tuple `(owner, resource_id, revision, content_digest)`, where
`content_digest` is `sha256:` plus 64 lowercase hexadecimal characters.
`WorkReleaseRef` retains the owner's `(owner, repository_id, item_id,
release_digest)` identity; Sprintctl release digests use bare hexadecimal. Neither
reference resolves content, authenticates its source or determines currentness.
Callers must obtain references and expectations from the respective owners.

```python
from vuoro_evidence.workflow import (
    ProviderWorkflowBinding, RevisionRef, WorkReleaseRef, bind_workflow,
)

# owner_binding is obtained through Sovite's supported compile endpoint or CLI.
# The expected values come from the requested work and installation, independently
# of the candidate binding. Copying expectations from a candidate proves nothing.
provider = ProviderWorkflowBinding.from_dict(owner_binding)
record = bind_workflow(
    provider,
    work_release=WorkReleaseRef.from_dict(owner_release_reference),
    requirements_ref=RevisionRef.from_dict(expected_requirements),
    session_policy_ref=RevisionRef.from_dict(expected_session_policy),
    workflow_revision=expected_workflow_revision,
    installation_id=expected_installation_id,
    bundle_generation=expected_bundle_generation,
    input_class=expected_input_class,
    expected_provider_binding_digest=expected_provider_binding_digest,
)
export = record.as_dict()
```

`bind_workflow` requires expected requirements, policy, workflow revision,
installation, bundle generation and input class. The class identifier is opaque;
Sovite owns its meaning and boundary admission. An optional independently obtained exact
provider digest additionally binds every resolved role and destination. A
mismatch raises `WorkflowBindingMismatch` with stable field names. The provider
snapshot is stored as immutable canonical JSON bytes; editing either the input
or an exported mapping cannot change an existing record. The exported
`vuoro-workflow-binding/v1` digest covers the work identity and complete provider
snapshot. Both provider and Vuoro binding digests hash canonical JSON excluding
only their own `binding_digest` field: sorted keys, compact separators, UTF-8,
unescaped Unicode and no non-finite numbers. The outer digest includes the
provider's digest. These structural checks do not authenticate either owner.

The standard-library CLI makes the same operation available without installing
Sovite or a runtime into the evidence package:

```bash
python -m vuoro_evidence.workflow bind \
  --provider-binding sovite-binding.json \
  --work-release work-release-ref.json \
  --requirements-ref requirements-ref.json \
  --session-policy-ref session-policy-ref.json \
  --workflow-revision "$EXPECTED_WORKFLOW_REVISION" \
  --installation-id "$EXPECTED_INSTALLATION_ID" \
  --bundle-generation "$EXPECTED_BUNDLE_GENERATION" \
  --input-class "$EXPECTED_INPUT_CLASS" \
  --expected-provider-binding-digest "$EXPECTED_PROVIDER_BINDING_DIGEST" \
  --output work-workflow-binding.json

python -m vuoro_evidence.workflow verify \
  --binding work-workflow-binding.json \
  --provider-binding separately-obtained-sovite-binding.json
```

`bind` writes stdout when `--output` is absent; a named output must be a new file.
`verify` checks the saved join and optionally compares a separately obtained
provider snapshot. Success reports `structurally_consistent`,
`source_authentication: not_performed` and `dispatch_authorized: false`. A mismatch,
malformed record or I/O failure exits 2. Verification performs no live owner lookup.
Retaining the exact prior binding supports reconstruction; selecting an earlier
binding for new work still requires current owner admission and a current release.

### Selected configuration versus observed execution

A definition or binding cannot establish that any session ran with it.
`WorkflowBindingObservation` separately requires a `binding_ref` and a
`provider_receipt_ref`, both `RevisionRef` objects. A caller may attach it to
`RunManifest.workflow_observation` after obtaining the referenced owner evidence.
This changes the run's composition digest; absent observations preserve existing
manifest serialization and digests exactly. The evidence package does not inspect
the receipt, infer execution success, accept work or promote an observed model to
an authenticated identity. The caller must verify the receipt's work/run and
binding correlation through its owner.
