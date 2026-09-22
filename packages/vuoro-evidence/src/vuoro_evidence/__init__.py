"""EvidenceSet consumer — Vuoro's claim vocabulary, reducer and decision path.

`core/` is host-agnostic: it knows claims, grants, validity windows and
decisions, never which host produced them. `ingress/` holds registered
decoders that turn a profile's wire objects into claims. The boundary is
tested: no ingress vocabulary may appear in `core/`.
"""
from .core.model import (Claim, ClaimType, Decision, DecisionKind, EffectGrant, EvidenceExpired,
                         EvidenceItem, EvidenceSet, GrantUse, ValidityBasis, ValidityWindow)
from .core.reducer import Ledger, reduce
from .core.decision import RerunQuestion, decide_rerun
from .core.chain import (ChainBreak, ChainBreakReason, ChainVerification, entry_digest, link,
                         verify_chain)
from .core.set_builder import EvidenceSetBuilder, verify_evidence_set
from .run import (ObservedProfile, RunManifest, RunManifestError, UnknownRunError, index_runs,
                  resolve_run)

__all__ = [n for n in dir() if not n.startswith("_")]
