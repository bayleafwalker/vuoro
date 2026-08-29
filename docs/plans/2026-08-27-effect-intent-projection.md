# Effect-intent projection correction

**Status:** planning correction; owner ratification required; no implementation
authorization  
**Date:** 2026-08-27  
**Amends:** `2026-08-22-long-term-direction.md` §§5, 11, and 15  
**Companion delivery plan:**
`bayleafwalker/agentops/docs/plans/agentops/2026-08-27-imperative-effect-correlation-pilot.md`

## Decision

The first split-brain framing put the single-writer boundary at the resource
node. At cluster granularity that becomes a cluster-wide mutex: one control-plane
upgrade would prevent unrelated applications from being changed even when their
effects commute. That is the wrong invariant.

Parallel work remains the default. A blocking decision belongs only to a
specific pair of effects that cannot safely commute, evaluated against their
target relation, phase, and observed state. Existing DevOps mechanisms already
make most of those decisions. Vuoro must not reproduce their leases, revisions,
or fencing tokens in a second authority plane.

The residual gap is narrower: an imperative effect can bypass the Git gate and
remain invisible to another session that is observing the consequences. Vuoro
should define the semantics of a wrapper-derived effect-intent projection so a
consumer can correlate those effects. The projection is advisory by default. It
adds no new denial path.

## Map the vocabulary before adding a mechanism

The mapping is by safety obligation, not by identical API shape.

| Harness vocabulary | Existing owner and mechanism | Consequence for Vuoro |
| --- | --- | --- |
| split-brain exclusion | etcd/Raft quorum and term rules; Pacemaker/STONITH for dual-primary hazards | Observe the native result; do not issue a parallel universal epoch. |
| conflicting object writes | Git protected refs and merge queues; Kubernetes revision preconditions, field ownership, admission, RBAC, and controllers | Preserve the target decision and evidence. Do not turn the cluster into the locked object. |
| scoped effect authority | Kubernetes RBAC, cloud IAM, short-lived credentials, approval and break-glass paths | `EffectGrant` records portable authority lineage; the target remains the enforcer. |
| maintenance envelope | rollout and maintenance records, disruption budgets, health checks, and operator runbooks | Project the expected symptoms and abort conditions to affected consumers. |

If an imperative surface genuinely lacks a safety mechanism, that defect belongs
at the surface. A generic Vuoro fencing epoch would be useful only if the target
validated it; adding the same check to Vuoro without target enforcement would be
ceremony.

## Contract correction

No sixth ledger object is added. The existing contract set remains:

- `WorkRelease` for planned intent and acceptance;
- `EffectGrant` for principal-bound consequential authority;
- `EvidenceSet` for factual attempted and observed effects;
- `Decision` for interpretation, exception, acceptance, and rollback;
- `ExperimentRecord` for change memory.

A stable value type and one versioned, non-authoritative specification support
the projection.

### `ResourceRef` and `ResourceGraphRevision`

`ResourceRef` is a stable URI-shaped value used by the contracts above. A
`ResourceGraphRevision` is a content-addressed specification or compiled
projection containing typed, directed dependency and propagation edges, their
sources, and validity. It may be incomplete and must say where it is incomplete.
It is not a graph database requirement and not a writable source of operational
truth.

The first vocabulary needs only enough identity to describe the homelab path:
Talos nodes and etcd members, Kubernetes API and control-plane availability,
Flux reconciliation, Kustomizations, namespaces, workloads, and the Git paths
that supply desired state. Propagation must remain typed. A control-plane upgrade
can affect API clients and reconciliation without implying that every running
workload is unhealthy.

### `EffectIntentProjection`

This is a view over existing records plus graph derivation, not another durable
authority object.

```yaml
effect_intent_projection:
  session_ref: session:A
  graph_revision: sha256:...
  planned:
    work_release_ref: ...
    effect_grant_ref: ...
  attempted:
    - evidence_ref: ...
      touched: [talos://node/cp-2]
      operation: upgrade
  propagates:
    - resource_ref: k8s://control-plane/api
      via: api-availability
  declared:
    intent: control-plane rolling upgrade
    phase: disruptive
    expect: [api unavailable for at most 60 seconds]
    abort_if: [etcd quorum lost]
    until: 2026-08-27T08:30:00Z
```

Wrappers derive the session, principal, operation, touched resources, time, and
evidence references from actual tool requests and receipts. The session may add
purpose, phase, expected symptoms, duration, and abort conditions because those
cannot be inferred reliably. Self-report never supplies authority or proof that
an effect occurred.

`EvidenceSet` remains factual. Labels such as expected, envelope exceeded,
conflicting, and unexplained are consumer-side interpretation and, when durable,
belong in a `Decision`. Informational session presence may help routing but never
confers authority.

## Delivery sequence

1. Freeze the small `ResourceRef` vocabulary and one graph fixture for the Talos
   control-plane-to-Flux path.
2. Define wrapper inputs and receipts for planned, attempted, and observed
   effects without capturing prompts or reasoning traces.
3. Build an intersection projection that emits a bounded notice with evidence
   references at a consumer's next relevant tool boundary.
4. Replay a recorded or synthetic upgrade trace before any live trial.
5. Run the next separately authorized Talos control-plane upgrade with an
   application session active.
6. Add enforcement only if the trial identifies a concrete non-commutative
   imperative surface that existing tooling does not already fence.

An A2A protocol is deferred until a second independently operated harness needs
the contract. The first consumer can use an AgentOps projection.

## Acceptance and falsifier

The hypothesis is that Session B can correlate transient cluster instability
with Session A's Talos upgrade from the derived projection and continue or
escalate according to the declared envelope. It fails if B still raises an
unexplained-instability escalation while the relevant projection and graph path
are available.

The pilot must also show:

1. a local-only application session receives no cluster-maintenance notice;
2. a session whose observed or planned scope intersects API or Flux
   reconciliation receives one bounded notice, not a transcript;
3. an exceeded envelope still escalates and cannot be cleared by A's assertion
   alone; target health evidence or an operator `Decision` closes it;
4. missing graph edges remain visible as unknown rather than broadening to
   `k8s://*`;
5. the projection cannot grant, renew, deny, or replay an effect; and
6. the PR introduces no new target-side fencing, graph service, session mutex,
   or A2A dependency.

## Non-goals

- serializing all work that reaches one cluster, repository, or namespace;
- replacing merge queues, admission, RBAC, controller ownership, etcd quorum,
  STONITH, GitOps, or ordinary separation of duties;
- inferring intent from hidden reasoning or process-hunting behavior;
- treating an agent-authored maintenance notice as evidence;
- making awareness a prerequisite for safe target behavior.

