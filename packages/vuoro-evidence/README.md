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
