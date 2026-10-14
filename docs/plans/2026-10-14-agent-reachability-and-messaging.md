# Agent discovery and messaging across harnesses: assessment

Status: assessment, 2026-10-14. Proposed; it authorizes no build, deployment,
register change, repin or publication. Written against this repository at
`c5b9a8c`. Vendor facts in section 3 come from web search on 2026-10-14 and
were not checked against primary sources; recheck any of them before a
decision depends on it.

## 1. The question

Roll an agent discovery and communications service into vuoro.cloud covering
Claude Code (local, cloud, Cowork, Routines), Codex (CLI, cloud), OpenCode,
and devbox sessions. Backbone on A2A. Authentication and authorization through
the existing cred-broker where needed. Sub-dispatch allowed. Interactive and
non-interactive setups kept separate.

The question decomposes into five: who may be found, who may be addressed,
who may ask whom to do work, under which identity, and where the runtime can
physically be reached. The last one decides the shape (section 3). The record
already decides several of the others (section 2).

## 2. What the record already decides

| Decision | Where | Consequence here |
|---|---|---|
| The multi-agent coordination layer is retired: "the shape was built as an experiment, run against a fair control, and lost." | register `meta-layer`; TS-2, TS-3 | The service may route and record. It may not plan, assign, reconcile or supervise. Any operation that chooses which agent does what is the retired shape. |
| Hybrid dispatch is retired: execution, sandboxing and model choice are harness-native. | register `hybrid-dispatch`; TS-2 | Sub-dispatch is a request delivered to a runtime that already exists or that a provider starts. Vuoro runs no worker. |
| "Watch ACP, not A2A. A2A v1.0 is real but solves inter-organizational federation, which you do not have." The one A2A artifact (`hostproto-a2a-worker`) is retired; its register row notes the creep was "in deployment shape rather than vocabulary". | rebuild 2026-09-19 section 6; register | A2A as wire vocabulary is consistent with the record. A2A as a federation backbone reverses a recorded judgement and must be written down as a reversal with its reason (section 3 gives one). |
| Both surfaces: a narrow public MCP surface for interactive runtimes and the Managed Agents self-hosted poller for unattended runs, served from vuoro.cloud. | edge plan 2026-09-20 section 10, amended 2026-09-22 | The interactive versus non-interactive split already exists at the transport. This design extends it; it must not add a third path. |
| Split horizon: the coordination horizon is vuoro.cloud primary; the protected horizon holds digest-bound acceptance, mint-once principals (`issuer:subject:epoch`) and cred-broker grants. | register `actionq`, amended 2026-09-30 | Directory and mailbox are coordination-horizon objects. Nothing in them applies an effect. |
| cred-broker is active and the authority boundary holds both ways: no Vuoro package imports it, no cred-broker source imports Vuoro. | register `cred-broker`; estate section 5.4 | Compose its API. Put no authorization policy in the mailbox. |
| vuoro-cloud is on hold; waking condition is the Sprintctl `served_profile` emission. | register `vuoro-cloud` | Building the hub there needs the row woken, or this work recorded as what wakes it. |
| A worker outside the perimeter "may read, may take an exclusive assignment, may record what it did, and may propose a change. It may not apply one, and it holds no credential that could." | reachability note 2026-09-19 | Messages are proposals and observations. Never effects. |
| Cross-harness continuation exists as `handoff/v1` (file, atomic single-successor ack, ssh for cross-host) plus `work-link/v1` (read-only locator, successor registers its own run). The successor is woken by local `claude -p` or `codex app-server --stdio`. | estate section 4.2; `docs/architecture/work-link-v1.md` | The "wake the next worker" step is host-local today. A cloud session cannot start or receive a continuation. That is the gap a messaging layer fills. |

The honest summary: the record has already rejected a coordinator twice and
sidelined A2A once. What survives those decisions is narrower than "agent
discovery and communications service": a **directory** that projects who is
registered, and a **mailbox** that carries A2A-shaped tasks between
principals, where every work-state change still goes through the existing
owner operations under the receiver's own run.

## 3. The structural fact that decides the shape

No runtime in this estate can accept an inbound connection, except
vuoro.cloud.

| Runtime | Inbound | Outbound | Interactivity | Provider-native channel |
|---|---|---|---|---|
| Claude Code cloud, Cowork, claude.ai, Routines | none | public HTTPS, MCP connector over OAuth | Cowork and claude.ai interactive; Routines and cloud sessions unattended. Sessions expire while waiting on a tool approval or sign-in. | Anthropic's own session bus: list, create, message, interrupt and schedule sessions. Claude to Claude only, provider-owned. |
| Claude Code on the workstation | none | LAN, cluster, forges, WAN | interactive | ACP adapter `claude-agent-acp` |
| devbox sessions as `agent` (Claude, Codex, OpenCode) | none | three LAN /32s; WAN "so OPNsense decides" (estate section 5.2) | non-interactive by construction: `bypassPermissions`, pinned contract, no guards | ACP adapters; `codex app-server --stdio`; `opencode serve` on LAN |
| Codex cloud | none | none documented; agent-phase internet blocked | unattended | none. `codex mcp-server` was removed in September 2026; Codex remains an MCP client only. |
| OpenCode | `opencode serve` HTTP on a LAN address, basic auth | LAN | either | OpenAPI 3.1 session API; ACP agent, not yet an ACP client |
| Managed Agents | none | Anthropic's queue | unattended | `vuoro-worker` poller (exists) |

A peer-to-peer A2A mesh, where each agent serves its own card and task
endpoint, cannot exist here. A2A push notifications need a webhook receiver;
only vuoro.cloud has one. So the only viable topology is hub-and-spoke:
vuoro.cloud hosts one A2A-shaped server, and every runtime is a client that
polls it, or is pushed to through a provider-native adapter.

That is why "use A2A as the backbone" and "watch ACP, not A2A" are both
right once the layers are named:

- **A2A** supplies the hub's shapes: agent card, task with its state machine
  (`submitted`, `working`, `input-required`, `completed`, `failed`,
  `canceled`), messages with parts, artifacts. These are worth adopting
  verbatim so that an external A2A client could talk to the hub later without
  a second contract.
- **ACP** supplies the host side: the thing that polls the hub on a devbox or
  workstation and drives Claude Code, Codex or OpenCode as a local process.
  The ACP registry lists all three. This replaces the hand-rolled `claude -p`
  and `codex app-server` paths in `handoff_codex.py`.
- **Provider buses** (Anthropic sessions, Managed Agents) are delivery
  adapters, not identity and not the record.

The reversal to record, if the operator agrees: A2A was dismissed as
federation. It is adopted here as vocabulary at one hub, with federation
explicitly out of scope until an external A2A client exists (a greppable
waking condition, section 7).

## 4. What it could enable

Each row names what exists today and the step that is missing.

1. **Continuation a cloud session can start.** Today `work-link/v1` lets a
   successor read the predecessor's delta, and `handoff/v1` carries the
   memory, but the successor is woken by a local command on the same host.
   With a mailbox, a Cowork session on a phone makes the link, posts a
   `continue` task addressed to a devbox principal, and the attach poller
   wakes a Codex or OpenCode successor that registers its own run naming
   `predecessor_run_id`. The record is unchanged; only the wake crosses the
   boundary.
2. **Behind-the-wall execution requested from outside.** A cloud session that
   cannot reach the cluster proposes; a non-interactive devbox session with
   forward reach performs; the result returns as evidence appended under the
   performer's run. This is the reachability note's table ("what crosses, what
   never crosses") implemented rather than asserted.
3. **A live inventory.** Who is working on which item, on which host, under
   which harness and model, interactive or not, and what they will accept.
   Today this is reconstructible after the fact from auditctl
   `workflow.session` rows and sprintctl leases, and the cockpit that showed it
   was retired. The reachability note says the one artifact to keep at any
   scale is the inventory; the directory is its live form.
4. **Independent review by another model family, bound to the item.** The
   weekly-lanes plan (W2-4) wants one extra review pass on a flagged unit.
   Posting a `review` task to a Codex principal and getting the finding back
   as an evidence item is that pass, with the reviewer's identity recorded.
5. **Rate-limit failover with a notify step.** Edge phase E4 parks the claim
   and records which model family the next attempt used. The mailbox is how
   the next family learns there is a parked claim, without Vuoro choosing it.
6. **`input-required` escalation.** A non-interactive run that hits a
   question currently dies or waits until its session expires. With the
   interactivity split, it transitions the task to `input-required`, the hub
   routes it to an interactive session of the same owner, and the answer
   comes back as a message. The non-interactive run never blocks.

What it does not enable, by decision: scheduling, placement, model routing,
worker supervision, a second credential broker, or any work-state change that
bypasses sprintctl's served operations.

## 5. Structure

### 5.1 Three objects, all coordination-horizon

**Registration and card.** A registration is an existing `session.binding`
evidence row plus a liveness lease with `work.lease` semantics: heartbeat,
expiry, no kill, because you cannot reliably kill what you did not start. The
agent card is a derived, rebuildable projection of live registrations, never
an authority, on the same footing as the knowledge catalog. Fields: principal,
harness and version, host class (workstation, devbox, cloud, managed-agents),
interactivity class, accepted task kinds, reachability (poll-only or
push-capable adapter), bound repositories, current run and item. Cards are
signed by the hub, not by the agent.

**Task and message.** A2A shapes. Every task carries a `context_id` that is a
sprintctl item or run reference; a task with no work reference is refused
(section 8, shadow queue). Messages are bounded (the edge's 64 KiB body limit
is the precedent) and carry evidence references by digest, never transcripts
or raw output. The mailbox is append-only and outbox-shaped; vuoro-cloud
already runs an outbox-driven tenant controller and needs no new store
technology.

**Delegation.** A task of kind `dispatch` carries a delegation chain:
requester principal, requester run, target principal, authorities requested,
expiry, depth. The chain is a record. Authority comes from the target's own
credential, bounded by the intersection with the requester's (section 5.2).

### 5.2 Identity and authorization

- **Interactive runtimes** authenticate as today: gateway OAuth, Ed25519
  assertion, one-use `jti`, edge proofs. Three new authorities, each a row in
  the edge's scope table: `agent:read` (discover), `agent:message`,
  `agent:dispatch`. The existing rule holds: authorities must be exactly what
  the scope table uses; broader is refused, never narrowed.
- **Non-interactive hosts** authenticate through cred-broker. The devbox or
  workstation attach poller holds a cred-broker session bound to its mTLS
  certificate fingerprint and requests one new capability, `agent.mailbox`,
  which cred-broker translates into a short-lived gateway credential. This is
  the only cred-broker change, and it should go through the reuse/gap matrix
  pattern the control-plane plan already prescribes (CRED-000) rather than a
  design decision here. The principal is the mint-once `issuer:subject:epoch`.
- **Every message carries the producer principal**, stamped by the hub on
  ingest. The receiving harness treats message content as data from a peer:
  it holds no instruction authority, the same stance this estate takes toward
  pull-request comments and webhook payloads.
- **Delegation never widens.** The effective authorities of a dispatched run
  are the intersection of the requester's and the target's. Depth is capped
  (two is enough for every scenario in section 4). The packet carries no
  claim token and no provider credential (the hybrid-dispatch rule); the
  target fetches its own through cred-broker under its own principal.
- Per-session principals and step-up are S7 and deferred while D2 stands.
  Non-interactive dispatch arguably wants a per-run principal so that a
  dispatched run's evidence is attributable to the dispatch and not only to
  the host. Whether this design moves S7 up is an operator call (section 9).

### 5.3 The interactive versus non-interactive contract

| Property | Interactive | Non-interactive |
|---|---|---|
| Human present | yes | no, by construction |
| May receive `input-required` | yes | no; a task needing input transitions and routes to an interactive owner |
| May block waiting | never; sessions expire while waiting. Every mailbox operation returns immediately with a handle. | never |
| Must hold a liveness lease | no (the gateway session is the liveness) | yes; expiry releases its tasks to `submitted` |
| Terminal reason | optional | required, from the shared vocabulary (`completed`, `process-exit`, `start-failed`, `cancelled`, `timeout`, `usage-limit`, `crash-inferred`) |
| May dispatch to | non-interactive, interactive | non-interactive only, depth-capped; to interactive only as `input-required` escalation |
| May approve step-up | yes, when S7 lands | never |
| Credential path | gateway OAuth | cred-broker capability |

### 5.4 Delivery adapters, by runtime

- **Interactive Claude (Cowork, claude.ai, cloud sessions):** MCP edge tools,
  poll-only: `list_agents`, `describe_agent`, `send_agent_message`,
  `receive_agent_messages`, `dispatch_to_agent`, `answer_input_required`.
  Same shape as `list_ready_work`: a row in the scope table, strict emission,
  no caching across callers.
- **Anthropic session push (optional):** an adapter in vuoro.cloud that uses
  the provider's session API to create or message a Claude session when a
  task is addressed to it. Provider-owned, Claude-only; delivery, not
  identity.
- **Managed Agents:** the same tools on `vuoro-worker`'s internal MCP server.
- **devbox and workstation, non-interactive:** a small attach poller that
  holds the cred-broker session, heartbeats the registration, polls the
  mailbox, and drives the harness over ACP (`claude-agent-acp`, `codex-acp`,
  `opencode acp`) or OpenCode's server API. It belongs in agentops beside
  `handoff.py`, not in `vuoro-client` (transport-only) and not in Vuoro.
- **Codex cloud:** none, unchanged from the edge plan: "building toward them
  would be building toward a maybe."

### 5.5 Placement by repository

| Repository | Owns |
|---|---|
| vuoro-cloud | the hub: directory, mailbox, card endpoint, delivery adapters, the new authorities in its OAuth scope map |
| vuoro (this repository) | contract text and JSON schemas (card projection, task envelope, delegation chain); the provider-neutral conformance suite, beside the lease and ledger suites; the edge tool rows; `vuoro-worker` internal tools |
| sprintctl | nothing new, unless the owner wants a `dispatched_by` reference on run registration. Work state changes only through existing served operations. |
| cred-broker | one capability and its receipt kind |
| agentops | the attach poller, the hooks that register and heartbeat, migration of `handoff/v1` wake to a mailbox task |
| appservice | deployment; outside this note |

## 6. Build or buy

| Option | Identity fit | Reach | Boundary | Cost | Verdict |
|---|---|---|---|---|---|
| A. A2A SDK server runtime inside vuoro.cloud | would need its security schemes mapped onto gateway assertions | hub only, same as B | the SDK's task store becomes a second store | medium, plus a daemon shape the retired worker already showed | defer until an external A2A client exists |
| B. In-built mailbox and directory in vuoro.cloud using A2A shapes, Postgres outbox | native: gateway assertions and cred-broker principals as they are | hub; every runtime polls or is pushed by an adapter | mailbox holds no work state; sprintctl untouched | small; the outbox pattern exists | **recommended** |
| C. Provider-native only: Anthropic session bus, Managed Agents, OpenCode serve | three identity systems, none the estate's | Claude to Claude, or LAN only | no shared record of who asked whom | low to build, unbounded to reason about | use as adapters under B, never as the record |
| D. Generic bus (NATS, Redis streams) | none; identity bolted on | hub | a new infrastructure component that speaks no principal | medium, ongoing | rejected at this scale |
| E. A hosted agent-registry product | external | external | the directory leaves the estate | subscription | rejected; the inventory is a control, not a convenience |

B plus C-as-adapters. The decisive reasons: identity already exists at the
gateway and at cred-broker and must not be duplicated; Postgres outbox is
sufficient for one operator and a handful of concurrent agents (the register
names sustained concurrency above five workers as the threshold that would
reopen durable execution, and nothing here changes that); and the A2A shapes
cost nothing to adopt while keeping option A open.

## 7. Sequence and gates

**Gate 0, the record.** Register rows for the hub and the attach poller with
waking conditions written as artifact tests. Record the A2A reversal (section
3) in the rebuild plan. Decide whether this wakes `vuoro-cloud` or waits on
its `served_profile` condition. Nothing below starts before this.

**R1, directory only.** Registration from the existing `session-binding`
hook plus a lease; `list_agents` and `describe_agent` on the edge; the attach
poller heartbeats from devbox. Read-only, reversible by GitOps rollback.
Conformance: an expired lease disappears from the projection; a card never
carries a credential or a transcript reference; a broader authority is
refused.

**R2, mailbox, poll-only.** Send, receive, task state machine, `context_id`
refusal rule, bounded parts. Interactive through the edge, unattended through
`vuoro-worker` and the attach poller. Conformance: append-only; a message is
never re-delivered as a different principal; a task cannot leave `submitted`
without a lease-holding or gateway-authenticated receiver.

**R3, dispatch.** Delegation chain, intersection rule, depth cap, expiry,
result returned as an evidence item under the target's own run. `handoff/v1`
successor wake migrates to a `continue` task; the ssh cross-host ack retires
with it. Conformance: a dispatched run's effective authorities never exceed
the requester's; a packet with a credential is refused at ingest.

**R4, push and external clients.** Anthropic session push, signed cards, and
option A, each on its own waking condition: a repository outside the estate
declares an A2A client dependency, or a provider confirms webhook delivery
into a sandbox.

Each stage is independently useful. R1 alone answers whether the inventory
changes how the operator works, and is the cheapest honest test of the whole
idea.

## 8. Risks and falsifiers

- **Shadow work queue.** The mailbox becomes where work lives. Rule: every
  task names a sprintctl item or run, refused otherwise. Falsifier: a task
  older than one lease period with no owner-side run referencing it.
- **Coordinator by accretion.** Someone adds "assign to the least loaded
  agent." The register's `meta-layer` waking condition applies in full; the
  operation list in section 5.4 is closed, and adding a selection operation
  reopens that row.
- **Injection through messages.** Treated as data with a producer principal;
  no deployed product prevents injection as an enforcement boundary, so the
  control is reach (default-deny egress, no credentials in the packet), not
  classification.
- **Replay and forgery.** One-use `jti` and edge proofs already cover the
  interactive path; the hub stamps the producer on ingest so a sender cannot
  claim another principal.
- **Expiring sessions.** Every operation non-blocking; long work is a handle.
- **devbox egress.** Whether devbox can reach vuoro.cloud depends on
  OPNsense, not on the host allowlist. Measure it. If it cannot, the attach
  poller runs on the workstation, which has forward reach, consistent with the
  reverse-ssh decision.
- **Cost.** Every poll is a tool call. Use `ttlMs`, and remember Routines may
  start without connectors loaded.
- **Two identities per agent.** A gateway subject and a cred-broker principal
  for the same host must bind to one principal string in the card, or the
  inventory lies.

## 9. Open questions for the operator

1. Record the A2A reversal as "A2A shapes at the hub, ACP at the host,
   federation out of scope"?
2. Wake `vuoro-cloud` for this, or stage R1 on vuoro-shared first? The edge
   decision's blast-radius argument says vuoro.cloud.
3. Does non-interactive dispatch move S7 (per-session principals) up while D2
   stands?
4. Can devbox reach vuoro.cloud today? One measurement decides where the
   attach poller runs.

## Sources outside the repository

Checked by web search on 2026-10-14, not against primary sources:
[A2A specification](https://a2a-protocol.org/latest/specification/) (1.0.0
listed as latest; Linux Foundation governance; signed agent cards;
extensions), [ACP registry](https://zed.dev/blog/acp-registry) and
[OpenHands ACP agents](https://docs.openhands.dev/openhands/usage/agent-canvas/acp-agents)
(adapters for Claude Code, Codex and OpenCode),
[Codex changelog](https://developers.openai.com/codex/changelog/) (`codex
mcp-server` removed; MCP client retained),
[OpenCode server](https://opencode.ai/docs/server/) (`opencode serve`, session
API, basic auth).
