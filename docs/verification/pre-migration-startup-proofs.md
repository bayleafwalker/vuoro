# Pre-migration startup proofs

These are specialized historical release proofs produced by
[`scripts/verify_pre_migration_startup.py`](../../scripts/verify_pre_migration_startup.py).
They declare `vuoro-pre-migration-startup-proof/v1`, rather than the shared
`verification-result/v1` schema.  They intentionally live outside
`verification/results/`, whose contents are discovered by the shared AgentOps
verification-artifact validator.

The original payloads are retained byte-for-byte for inspection and provenance:

| Proof | Source commit | SHA-256 |
| --- | --- | --- |
| [item #2092](../../verification/specialized/pre-migration-startup/vuoro-pre-migration-startup-item-2092.json) | `fb71d61` | `50fc99b626a9bb44af4f1367906de7532496c16a8d0c01a17268795966295e03` |
| [v0.1.34 pre-hardening](../../verification/specialized/pre-migration-startup/vuoro-published-image-startup-v0.1.34-prehardening.json) | `6590b17` | `b30a8cb22dd29cdb2eb3688f01c38a7769aaf40cd16ec3844d3dfe2b45899e53` |
| [v0.1.35 candidate](../../verification/specialized/pre-migration-startup/vuoro-published-image-startup-v0.1.35-candidate.json) | `509f68e` | `b438fbcd6a5cf96a115d57ae028a48d7c2300232a0b4c68e389de874971a8152` |

This placement is classification only: it neither changes the evidence payloads
nor broadens the generic validator's accepted schemas.
