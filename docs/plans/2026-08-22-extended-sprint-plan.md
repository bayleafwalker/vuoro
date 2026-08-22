# Extended sprint plan — composition v4 through W4 rescope

Horizon: roughly four weeks of working sessions. Spans `/projects/dev/vuoro` and
`/projects/dev/actionq`. Written as a handoff: a session picking this up cold should be able to
start at the first unchecked item without reconstructing context.

Operating assumption, stated because it changes the shape of everything below: **this is a
single-operator system.** Broad, breaking changes are preferred over compatibility machinery.
The thing to avoid is not breakage — it is a design that forces continuous small migrations and
test churn. Where a choice exists between a clean break and a staged transition, take the break.

## Where things stand

| Thing | State |
| --- | --- |
| ActionQ W3 (backfill, rebuild, export/restore) | merged, `227f55f` |
| Falsifier-coverage gate, round orchestrator, finding instrumentation | merged (actionq #36–#39) |
| ActionQ W4 scope (#40) | paused as draft, architecturally superseded, corrections retained |
| Vuoro composition v4 design freeze (#51) | open, both review channels closed, revised at `0284e91` |
| Vuoro v4 candidate | not started — this sprint |
| ActionQ W4 rescope | blocked on the candidate |

## Week 1 — land the freeze, start the loader

1. **Merge #51** once the revised head is reviewed. Everything both channels raised is either
   applied or explicitly marked proposed; nothing is outstanding that blocks a candidate.
2. **Port the falsifier gate into vuoro** (`tests/test_falsifier_coverage.py` from actionq,
   unchanged except `DOC_ROOTS`). It already passes against the freeze — verified by running the
   actionq copy against this repository — so this is a move, not a build. It makes §9 enforced
   rather than read, and it is the thing that will show coverage rising from 0.0 as the candidate
   lands.
3. **Port the round orchestrator** (`verification/run_round_checks.py`) and adapt the derived
   artifacts it refreshes: vuoro has no reachability manifest or action-resource packets, so the
   sequence reduces to gate → suite twice → build. Keep the ordering property; it is the whole
   point.
4. **Start the v4 loader beside v3**, not replacing it. `composition_v4.py` with the five record
   types, reading a support manifest and a profile lock. v3 stays loadable until the migration
   proof passes.

## Week 2 — the two rules that carry the freeze

5. **Uniform construction protocol.** `build(runtime: RuntimeConfiguration) -> Application` plus
   `register(registry, application)`. This is the single most valuable item in the sprint: it is
   what makes a fifth contract *composable* rather than merely declarable, and without it v4 does
   not unblock what motivated it. Expect it to be invasive — `create_composed_app` currently
   hand-wires four domains and carries a hardcoded audit exception that bypasses `_load_function`
   entirely. Break it rather than accommodate it.
6. **Validator rules 1–9.** Rules 7 (declarative contract set, uniform construction) and 8
   (the v3 invariants carried forward) are the ones with no v3 analogue to copy; the rest are
   ports. Rule 8 is easy to under-build — the four invariants it carries had real work to do.
7. **Falsifiers for 5 and 6 first**, then the rest. `v4-declarative-contract-set` and
   `v4-uniform-construction` are the two that falsify the freeze's reason for existing.

## Week 3 — migration proof and the reference profile

8. **Migrate the v3 reference composition** (7 locks, 4 descriptors) into a v4 `shared` profile.
9. **The equivalence proof**, which is now sharply defined: build a `CatalogRegistry` from the v3
   manifest and from the migrated v4 profile and assert the revisions are **byte-identical**,
   alongside set-equality of provider pins, sha256s, `execution/v1` operation hashes, bindings and
   the declared dependency closure, plus `migrated_from.manifest_sha256`. A v4 loader that
   perturbs registration order or the resource-kind set would otherwise silently invalidate every
   client's cached revision and trip ActionQ's `stale-catalog` fence fleet-wide.
10. **Proof cases**, wheel-first: `execution/v1` carried forward frozen and exclusive is the one
    that must pass this sprint. The external-provider cases (OpenBao `secret.lease/v1`, OTel
    `telemetry.export/v1`) exercise `image`/`chart` closures and probe conformance; **dropped from
    this sprint** — the 2026-08-22 EventStorming board (`docs/evidence/`) shows nothing observed
    needs them proven now, and they prove a different half of the ontology that nothing
    downstream waits on.

## Week 4 — W4 rescope in ActionQ

11. **Confirm or correct the three proposed federation properties** in the freeze's §3.2: owner
    of `federation.principal/v1`, its `global` scope against ActionQ's environment-partitioned
    principal keys, and the `project` scope for grants against an `authorities` set that has no
    project dimension. These are ActionQ-side facts and they are inputs to the rescope.
12. **Settle the Identity → `FederationPrincipal` mapping.** Still the largest open item in the
    whole tranche. Ownership equality turns on `principal_id` forever and v1 has no ownership
    transfer, so a reissuable actor string silently transfers ownership and the resulting data is
    unfixable. Decide the stable-identifier rule before any federation provider binds.
13. **Rescope W4 as authority-plane contracts**, reusing #40's retained corrections: the
    ActionQ-side additions (serving surface in its own module, catalog contract test, version bump
    and release, reachability entries), the execution-adapter re-validation against the new wheel,
    and the ordering constraint that backfill precedes any native `federation.create` grant.
14. **Reopen or supersede #40** with the rescoped document.

## What is deliberately not in this sprint

W5 in any form — live migration, deployment, credential and role fencing stay operator-owned.
W7 stays unauthorized. The Restate pilot stays unpackaged and separate. No Vuoro-owned runtime:
no runnerd, queue, placement, leases or harness adapters.

## The measurement track, running underneath

Capture `first_attempt_pass` and `collateral_breakage` per finding, one fix at a time, in
`docs/evidence/*-finding-records.jsonl`. Coverage on implement-eligible findings is 47% against a
60% threshold; `summarize_findings.py` reports `usable as a baseline: no` until it clears. This
sprint should carry it past the threshold on volume alone, at which point a cheap implement tier
becomes measurable against a real baseline.

Two results from the W3/W4 rounds worth carrying, because they shape what to trust:

- **Both review channels converged on the same primary defect in every round where both ran.**
  Independent agreement is a usable signal here, not a hope.
- **44% of findings were claim-level.** That share can never go to a cheap tier, which is the
  argument for keeping an expensive reviewer on claim-bearing diffs rather than only at plan time.

## Sequencing constraints that are not negotiable

- The Vuoro manifest shape must be settled before any ActionQ wheel is published. A released
  wheel cannot be amended; any later change costs a version bump, tag, release, manifest edit and
  full re-validate.
- ActionQ merges and publishes before the Vuoro candidate PR can be validated, because the
  manifest must name a wheel digest that already exists. The two repositories' pull requests are
  ordered, not parallel — and the design order is the reverse of the release order.
- Backfill completes before any native principal holds `federation.create` in an environment.

## Direction above this sprint

`2026-08-22-long-term-direction.md` is the directional freeze candidate this sprint serves. Its
§0 reconciles it with the v4 freeze (global revision preserved, `required` flag, ledger objects
as capability contracts, federation as three contracts). Its §11 places this sprint as
Priority 0; `WorkRelease` / `EffectGrant` / `EvidenceSet` / `Decision` contracts follow it and
are not in scope here. Nothing in that document reopens an item this sprint closes.
