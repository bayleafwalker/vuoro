# vuoro-evidence

The EvidenceSet consumer built for the HostProto validation phase
(hostproto-semantics `docs/EVIDENCESET_VALIDATION.md`, ADR-0013).

- `core/` — `EvidenceSet`, `EffectGrant`, `Decision`, the claim vocabulary,
  the reducer and the rerun decision path. Host-agnostic; the boundary test
  fails if any profile, adapter or host vocabulary appears here.
- `ingress/` — registered decoders. `hostproto` turns receipts, errors,
  observations and evidence refs into claims; `command-capture` turns an
  outctl capture manifest plus a collector-declared validity window into
  claims.

`EffectGrant` is input from ActionQ/federation. Grant use is a projection
of effect state in the reducer; no ingress edge can assert it.
