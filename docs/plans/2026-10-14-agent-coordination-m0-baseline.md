# Agent directory and inbox: M0 baseline and decisions

Status: M0 record for `docs/plans/2026-10-14-agent-coordination-roadmap.md`,
2026-10-14. Contract: `docs/architecture/agent-directory-and-inbox.md` and
the four `docs/architecture/agent-*-v1.schema.json` files. Input assessment:
`docs/plans/2026-10-14-agent-reachability-and-messaging.md` revision 2
(`41e927b`, branch `claude/pensive-cray-bszpta`), read in full. Where this
record and the assessment disagree, this record states what was measured.

Repository states read: vuoro `c555246`, vuoro-cloud `origin/main` `9b44e3b`,
sprintctl `9a8d356` (0.18.0), agentops `1165972`, cred-broker `origin/main`
`26d16b2`.

## 1. What exists, measured

Tags: **ran** means executed today; **read** means read in code.

### 1.1 Hub candidates

| Fact | Evidence |
|---|---|
| vuoro-cloud has no inbox, directory, session registry, change feed or work-link storage | read, grep across src, migrations, apps, tests and docs |
| Its outbox is a provisioning work queue: kinds `reconcile` and `tenant-migration`, foreign-keyed to workspaces, no retention, global `created_at` order | read `migrations/001_control.sql:96-109`, `controller.py:174-262`; ran `tests/test_controller.py`, 26 passed; outbox integration 122 passed and 1 failed. The failure is order dependent: `once()` claims the globally oldest row |
| vuoro-cloud register row is `hold`, released only by its owner, waking on a Sprintctl `served_profile` | read `docs/direction/disposition-register.yaml:217-242` |
| api.vuoro.cloud refuses every mutation (`POST /oauth/token` returns 503 `mutations-frozen`), which is why the claude.ai Vuoro connector cannot sign in | ran, curl ×3, 2026-10-14T10:12Z; cause read at `vuoro-cloud gateway.py:203,605-627` |
| vuoro-shared (`https://vuoro-shared.apps.kotona.app`) serves Sprintctl through vuoro-service and authenticates end to end from the workstation | ran `sprintctl doctor` and `next-work`, identity `workstation-vuoro`, 10:13Z |
| vuoro-shared resolves only on the LAN (`192.168.20.219`) | ran `getent hosts` |

### 1.2 Reachability per runtime

| Runtime | Version | Unauthenticated | Authenticated operation | Proves |
|---|---|---|---|---|
| Workstation | claude 2.1.299, codex 0.162.1, opencode 1.19.0 | api.vuoro.cloud OAuth metadata 200; vuoro-shared `/health/live` 200 | vuoro-shared served read 200; vuoro-cloud operator read 200 | connectivity and served read authentication. Send permission is not proven until M1 |
| devbox-vm as `agent` | claude 2.1.287, codex 0.160.0, opencode 1.18.33 | vuoro-shared 200; **api.vuoro.cloud TCP timeout** (IPv4) and no IPv6 route, while DNS resolves | not run | devbox can use vuoro-shared and cannot use vuoro.cloud today |
| Claude cloud / claude.ai | n/a | evidenced only by the connector reaching the gateway's token endpoint | blocked by the mutations freeze | nothing about send or read permission |
| Codex cloud | env `homelab-analytics` | task `task_e_6acf56263e10832389c784f3239db90f` submitted 10:15Z; its curl output is not exposed through the CLI | not run | unverified |

Measured 2026-10-14 10:12-10:17Z. A 200 proves connectivity only. No row
proves that a stopped runtime can be woken.

### 1.3 Harness integration

`opencode acp` is installed on both hosts. `claude-agent-acp` and `codex-acp`
are installed on neither. There is no ACP client anywhere in the estate, and
the sprint 545 `ACP-*` items were re-marked `reject`.

| Capability | `opencode acp` | `opencode serve` | `codex app-server` |
|---|---|---|---|
| Start | ran on both hosts | ran | schema only |
| Native id | `ses_…` returned | `ses_…` returned | `threadId`, schema only |
| Status | `session/list` | `GET /session/status` | schema only |
| Load or resume in a new process | ran `session/load` | not tested | schema only |
| Idempotent launch | **no**: two `session/new` calls with the same `_meta` keys returned two sessions | no | no key field |
| Discovery by correlation id | **no**: no list filter, generated titles | **yes**: `POST /session {"title": …}` then `GET /session?search=…` returned it | `thread/list` has `searchTerm`, schema only |
| Client crash | the stdio agent dies with its client; the in-flight turn ends `MessageAbortedError` | the server outlives its client; crash not tested | schema only |

### 1.4 Owner primitives that can be reused

| Primitive | Guarantee, as tested | Evidence |
|---|---|---|
| `work.run.register-v1` | one run per `(repo, workspace, principal, idempotency_key)`; same key and digest replays; optional `predecessor_run_id` must exist in scope | ran `tests/pg/test_run_continuation.py` 26 passed, `test_run_evidence.py` 55 passed |
| Predecessor link | **not exclusive**: two runs with different keys can name one predecessor | ran, scratch probe; non-unique `idx_run_predecessor_repo_predecessor` (`pg.py:3170-3174`) |
| Idempotency ledger | `(repo, workspace, principal, tool, key)`; digest conflict gives `idempotency-conflict`; ledger, effect and result commit in one transaction | ran `tests/pg/test_idempotency_ledger.py` 39 passed |
| `work.session-note.write-v1` | bounded, run-scoped, idempotent, touches no item state | ran `test_run_evidence.py:584` |
| `work.lease.*` | exclusive and fenced per item, **PostgreSQL only**; the fence is checked only by lease operations, not by edits, evidence, notes, effects or reservations | ran `tests/pg/test_work_lease.py` 189 passed |
| Reservations | advisory: overlap is reported, not refused | ran 33 passed; `reservation.py:1-12` |
| Effect intents | CAS on accept, reject and mark-applied; single-use attempt redemption | ran 123 passed |
| Item edit | `expected_revision` refused when stale (`item-edit-conflict`) | ran |
| Principal | `issuer:subject:epoch`; epoch bump revokes tokens and assertions | read `vuoro-cloud principal.py:3-4,52-100` |

## 2. Decisions

### 2.1 Placement: contract in Vuoro, store in Sprintctl, served on vuoro-shared

Vuoro holds the contract, the schemas and the conformance suite. Sprintctl
implements the operations and migrations as their store owner. vuoro-shared
serves them and is the single authoritative store for these record families
until Q1(b). Nothing is dual-written.

Basis:

- Vuoro packages own no migrations: "Domain state machines and migration
  assets remain in their owner repositories" (`AGENTS.md:11-13`).
- TS-1 puts Vuoro's semantics "inside sprintctl's served authority"
  (`agentops docs/plans/2026-09-17-target-state.md:39`).
- The 2026-09-30 resolution makes Vuoro hold the provider-neutral contract
  and conformance suite, with Sprintctl conforming at that edge
  (`disposition-register.yaml:676-690`).
- The transition rule allows vuoro-shared as the one named store per record
  family until Q1(b) (`agentops docs/architecture/2026-09-27-agentic-ecosystem-and-split-horizon.md:470-483`).
- vuoro-cloud "owns no work or action authority" (register :224-226), so the
  assessment's §5.6 placement of a message store there conflicts with its own
  row.

vuoro.cloud reaches these records through its planned Sprintctl
`served_profile` at the cutover. That is a profile change, not a data
migration.

Consequence: cloud runtimes cannot reach vuoro-shared (LAN only). M1 to M2c
run on the workstation and devbox. Cloud senders arrive with the Q1(b)
cutover or with the M4 expansion.

### 2.2 First host, harness and sender

- **Host adapter:** devbox-vm as `agent`, under devbox's existing vuoro-shared
  profile (`/home/agent/.config/vuoro`).
- **Harness integration:** OpenCode through `opencode serve` on loopback, not
  `opencode acp`. The successor run must outlive an adapter crash, and a
  launch must be discoverable after one. ACP fails both in measurement; serve
  passes discovery and is a daemon by design.
- **Sender:** an interactive Claude Code session on the workstation, under
  `workstation-vuoro`.

These are two runtime types under two principals, which meets M1's exit
condition.

### 2.3 Launch recovery

Neither harness offers idempotent launch, so the adapter uses mechanism 2
(discovery by a stable correlation identifier), backed by the owner:

1. The adapter persists a processing record before any external act.
2. It records `accepted` at the store. Only one instance of the principal can
   win.
3. It registers the run with key `inbox.<message_id>`. Retries converge on one
   `run_id`.
4. It persists the launch intent, then creates the OpenCode session titled
   `vuoro-msg:<message_id>`, then persists the session id.
5. On restart, a launch intent without a session id is resolved by title
   search. If found, it is adopted. If not found, it becomes `ambiguous`. That
   is shown in the adapter's status and the store's receipts, and it is
   **never relaunched automatically**.

Duplicate computation stays possible if someone relaunches by hand. Duplicate
owner effects do not: the run is one per message, and owner writes use
derived idempotency keys.

### 2.4 Contract decisions required by the roadmap

| Decision | Resolved in |
|---|---|
| Principal, runtime installation, run | contract §1 |
| Recipient addressing and tenant/project boundaries | contract §1, §3.1; `not-found` for out-of-scope records |
| Request-mode authorization | contract §3.3 (the receiver decides); adapter policy re-read before launch (§2.3) |
| Identity, immutability, versioning | contract §3.1; `schema` constants with a `/v1` suffix |
| Acknowledgment versus outcome | contract §3.3, separate receipt kinds |
| Retention, replay, cancellation, limits | contract §3.2, §3.3, §3.5; 64 KiB envelope |
| Inbox versus work links versus evidence | contract §3.4 |
| Existing change stream | **insufficient**: observable resources have no service-side store, replay or retention (`observable-resources.md:97-106`; client-only implementation), and vuoro-cloud has no change feed. The inbox carries its own `inbox_seq` cursor |

### 2.5 Authority used in v1

Directory and inbox writes use `work:evidence`, and reads use `work:read`.
Both already exist in served grants, so no grant change is needed.

This is a recorded compromise. A dedicated `agent:coordinate` authority is a
named gap for M3, before any principal outside the operator's hosts is
enabled.

## 3. Owners and dependencies

| Dependency | Owner | State |
|---|---|---|
| Contract, schemas, conformance suite | vuoro | this record; suite to build |
| Store operations and migrations, CLI verbs | sprintctl | to build: `work.agent.*`, `work.message.*`, CLI `agent`, `message`, `run context` and `run note` |
| Composition and served exposure | vuoro (service), appservice (rollout) | to build: adapter pin and schema qualification, following the `s4-*` compositions |
| Host adapter | agentops | to build: local SQLite processing records, systemd user unit |
| Harness | OpenCode `serve` | verified start and discovery; crash behavior to be tested in M2b |
| Authentication | vuoro-shared served profiles | verified on the workstation; devbox profile present, not yet exercised for writes |
| Principal revocation | gateway, principal epoch | exists; not mirrored in the directory (gap) |
| Exclusive work ownership | sprintctl `work.lease.*` | exists for leases only; **not relied on** by this design |

## 4. Gaps carried forward

1. Codex cloud egress is unverified. The submitted task's output must be read
   in the ChatGPT UI.
2. devbox cannot reach api.vuoro.cloud. This only matters at Q1(b).
3. The api.vuoro.cloud mutation freeze blocks every claude.ai connector
   sign-in. This is outside this package.
4. No principal revocation appears in the directory. The `work:evidence`
   reuse is noted in §2.5.
5. `opencode serve` crash and restart behavior is not yet measured.

## 5. Estimate for M0 to M2c

| Package | Repository | Size |
|---|---|---|
| Store operations, migrations, PostgreSQL tests, CLI, release | sprintctl | large: about 10 operations and 5 tables, following the run-evidence pattern |
| Conformance suite, reference provider, sprintctl binding | vuoro | medium |
| Composition, service release, vuoro-shared rollout | vuoro, appservice | medium; follows the `s4-*` precedent |
| Host adapter and tests | agentops | medium |
| M1 registration and M2c recorded run with induced failures | all | small once the above have landed |
