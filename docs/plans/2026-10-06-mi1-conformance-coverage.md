# MI-1 conformance coverage at declared owner horizons

This is the current source coverage supplement to the 2026-10-05 milestone
plan's snapshot. It changes no target claim or product acceptance bar. A
reference pass, a gap receipt or a test at one horizon does not qualify a
different owner/horizon. All new histories below are bounded example tests.

| Required behaviour | Concrete case | Qualification / remaining gap |
|---|---|---|
| Stale claims and retained superseded reports | service conformance `test_lease_contract.py::test_takeover_supersedes_old_heartbeat`, `::test_superseded_report_retained_without_settlement`, `::test_same_holder_cannot_resume_superseded_handle`; lease mutant in `test_oracle.py` | Existing reference and published native lease-owner bindings, mandated by CI. Lease stays outside the frozen generic federation contract. |
| Exclusive claims and no background stale effect | `test_lease_contract.py::test_live_lease_refuses_another_holder`, `::test_staleness_has_no_background_effect` | Existing native lease horizon, not a generic resource-owner certification. |
| Exact acceptance distinct from application/settlement | MCP-edge `test_effect_authority_contract.py::test_accept_binds_exact_revision_digest_but_is_not_application_or_settlement`, `::test_oracle_detects_false_settlement_on_acceptance` | Existing reference and published effect owner; pending work is preserved by acceptance/application recording. |
| Revision/digest conflicts preserve state | `test_effect_authority_contract.py::test_every_transition_refuses_wrong_acceptance_binding` | Existing reference and published effect owner. Resource-description CAS observation is separately in `resource_owner_scenarios.py::test_description_cas_preserves_aggregate_identity_and_refuses_stale_write`. |
| Provider ingestion exact retry and conflicting replay | `test_provider_ingestion_contract.py::test_committed_provider_reply_loss_reconnect_and_exact_retry`, `::test_same_provider_delivery_changed_artifact_refuses_without_history_or_identity_change`, `::test_distinct_provider_delivery_extends_chain_and_old_retry_preserves_new_tail` | New reference plus published native run/evidence API binding, four synthetic event families. Actual PG reconnection and API-only receipt/tail observations. No storage-layout oracle. |
| Public caller cannot reach protected apply | `test_effect_authority_contract.py::test_public_http_cannot_reach_protected_effect_transition`, `::test_public_http_oracle_detects_missing_pre_owner_gate` | Vuoro PR187 / CI37503178953; real reusable HTTP shell and published owner under declared test authority sets. Not live OAuth issuance, deployment or external effect execution. |
| Changed artifact cannot reuse approval | `test_effect_authority_contract.py::test_changed_patch_cannot_reuse_approval`, `::test_changed_patch_oracle_detects_ignored_acceptance_digest` | Vuoro PR186 / CI37501544792 and37501978309: patch-bearing canonical intent horizon. The released raw-artifact / protected-verifier binding required by P2/Track B remains unknown and unimplemented; this narrower case does not discharge that bar. |

The added provider contract is included in the existing `settlement-scenario`
CI step alongside ledger/effect contracts. It configures an isolated
PostgreSQL owner and verifies the immutable composition wheel. Unconfigured
owner skips cannot count as a pass; exact executed results are in
`verification/results/mi1-provider-ingestion-2026-10-06.json`.

The resource reference contract still covers broader resource-aggregate
behaviour than the published work owner. Existing owner scenarios marked
`conformance_gap` (trackers agentops#2603/#2604) remain nonqualifying: principal
reissue ownership fencing, opposite-relation cycle admission, general resource
commands, and mandatory catalog-revision behaviour at the legacy dispatch
horizon. They are not silently discharged by native effect-intent or provider
append tests. See the header and markers in `resource_owner_scenarios.py`.

P3 stays pending on active P2. Before milestone acceptance, implement and prove
the release/raw-artifact/protected-verifier link at its real owner boundary,
then the actual different-commercial-harness Track B case. No matrix row above
claims whole-milestone completion, production rollout, formal verification or
concurrency qualification from sequential examples.
