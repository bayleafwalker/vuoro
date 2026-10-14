# Agent discovery and agent-owned coordination across harnesses: assessment

Status: assessment, revision 2, 2026-10-14. Proposed; it authorizes no
build, deployment, register change, repin or publication. Written against
this repository at `c5b9a8c`. Revision 2 corrects revision 1 on four points
after review: the retired coordinator rows do not bear on agent-owned
coordination; the reachability argument rests on inbound only; A2A shapes
are an internal contract and reverse no decision; and dispatch is a separate
decision, not part of the proposal. Vendor facts in section 3 come from web
search on 2026-10-14 and were not checked against primary sources. Agentops
target-state claims (TS-1, TS-2) are quoted from review and from this
repository's references to them; the agentops repository was not readable
from this session, so recheck them there.

## 1. The question

Give agents a way to find each other and coordinate among themselves across
Claude Code (local, cloud, Cowork, Routines), Codex (CLI, cloud), OpenCode
and devbox sessions. No operator in the loop: the agents are the users.
Identity and authorization through the existing gateway and cred-broker.
Interactive and non-interactive setups kept separate. Sub-dispatch
permitted.

The question decomposes into: who can be found and with what confidence;
how a running agent learns something changed; how a stopped agent is started
again; under whose authority a request is acted on; and what the message
store guarantees when deliveries duplicate or a receiver crashes. Each has
its own section below.

## 2. What the record decides, and what it does not

**What the retired rows do not decide.** The `meta-layer` row retires "a
multi-agent coordination layer in which one session dispatches and
reconciles explorers, planners and orchestrators." The `takeover-experiment`
row records that "one Coordinator over five Sol workers, with the
coordinator owning task creation, claims, worktree allocation, cross-lane
decisions, merge order, evidence index and gate decisions" lost to a direct
Git handoff. Both describe a coordinator session: a meta-harness that owns
assignment and gates. Neither tests, and neither retires, peers that discover
each other and converge on shared state with no coordinator. Revision 1 of
this note read those rows too broadly.

**What does bear on it.**

| Decision | Where | Consequence here |
|---|---|---|
| "Coordination has exactly two jobs: stop two workers doing the same thing, and stop any worker doing something it was not granted. Everything else people put in this layer is scheduling." Leased claims with heartbeat and expiry; effect intent declared before work; no merge queue at this throughput. | rebuild 2026-09-19 section 7 | Agent-owned coordination is already the direction. Its primitives exist in the work owner: `work.lease.*` (fenced, one per item), advisory reservations, `work.effect.propose`, declared write scopes (WL-C2). What is missing is discovery and notification, not a new coordination authority. |
| TS-1 (as quoted in review) excludes assignment, scheduling, retry, supervision and expiry of execution intents from Vuoro; TS-2 leaves execution, sandboxing and model choice with harnesses. | agentops target state | The hub may hold a directory and a durable message store. It may not select a recipient, start a run, retry a failure, or choose a replacement model. Section 5.2 names who does each. |
| Hybrid dispatch retired. Frozen packets carry no claim token and no provider credential. | register `hybrid-dispatch` | Carried over as a rule for any message that asks for work. |
| Both surfaces: narrow public MCP for interactive runtimes, Managed Agents poller for unattended runs, served from vuoro.cloud. | edge plan 2026-09-20 section 10, amended 2026-09-22 | The interactive versus non-interactive split exists at the transport. Extend it; add no third path. |
| Split horizon: coordination horizon is vuoro.cloud primary; protected horizon holds digest-bound acceptance, mint-once principals, cred-broker grants. | register `actionq`, amended 2026-09-30 | Directory and messages are coordination-horizon objects. Nothing in them applies an effect. |
| cred-broker is active; the boundary holds both ways. | register `cred-broker`; estate section 5.4 | Compose its API. No authorization policy in the message store. |
| Vuoro standardizes reference, snapshot, change and delivery envelopes for domain-owned observable resources; change delivery is at least once, ordered per resource, with event IDs for deduplication. | `docs/architecture/observable-resources.md`, ratified | The notification primitive already has a ratified contract. Agent-to-agent "something changed on the item you depend on" is a subscription over the owner's change envelopes, not a new message kind. |
| A worker outside the perimeter may read, take an exclusive assignment, record, and propose. It may not apply, and holds no credential that could. | reachability note 2026-09-19 | Messages are proposals, requests and observations. Never effects. |
| "Watch ACP, not A2A." A2A "solves inter-organizational federation, which you do not have." | rebuild section 6 | Holds. Section 5.7 adopts A2A envelope shapes as an internal contract and commits to no interoperable A2A endpoint. No reversal is needed. |
| `vuoro-cloud` is on hold; waking condition is the Sprintctl `served_profile` emission. | register | Section 9 recommends how this interacts. |

## 3. Reachability, stated only as far as it is established

**Established: no runtime in this estate accepts an inbound connection,
except vuoro.cloud.** Cloud sessions, Cowork and Routines run in provider
sandboxes with no inbound route. Devbox is behind default-deny nftables with
no listener reachable from outside the LAN. Managed Agents workers poll. That
single fact fixes the topology: the hub is the only server; every runtime is
a client of it. Any peer-to-peer design is excluded before protocols are
discussed.

**Not established: outbound reach per runtime, deployment and
configuration.** Revision 1 wrote "Codex cloud reaches nothing." That was
wrong. The table below records what is documented and leaves the observed
column empty where no test has been run. Nothing in sections 4 to 7 depends
on a row that reads "unverified".

| Runtime | Inbound | Outbound, as documented | Observed test | Notes |
|---|---|---|---|---|
| Claude Code cloud, Cowork, claude.ai, Routines | none | public HTTPS; MCP connector over OAuth | the edge is reached from Cowork today (E1 served) | sessions count as inactive while waiting on a tool approval or sign-in and can expire; Routines may start without connectors loaded |
| Claude Code, workstation | none | LAN, cluster, forges, WAN | daily use | |
| devbox sessions as `agent` | none | three LAN /32s; WAN "so OPNsense decides" | **none; to measure** (section 9) | if vuoro.cloud is not reachable, the host adapter runs on the workstation |
| Codex cloud | none | agent-phase internet off by default; configurable per environment with a domain allowlist; setup phase has access | **none** | whether a cloud task can use an MCP connector or a plain HTTPS client against vuoro.cloud is unverified; `codex mcp-server` removal concerns an integration command, not reach |
| Codex CLI | none | host network | daily use | `codex app-server --stdio`; ACP adapter `codex-acp` |
| OpenCode | `opencode serve` on a LAN address with basic auth | host network | **none** | OpenAPI session API; ACP agent |
| Managed Agents | none | Anthropic's queue | **none against the vendor** (`vuoro-worker` README) | the poller exists; the vendor-side agent registration does not |

## 4. What it enables, for agents

Each row names the primitive that exists, the step that is missing, and
which of the three wake modes in section 5.5 it needs.

1. **Discover a peer on a dependent item.** Today an agent on item A, blocked
   on B, learns B is done only by re-reading sprintctl. With the directory it
   can see whether anyone holds a lease on B, under which harness and host,
   and with what freshness. Needs: directory read. Wake mode: none.
2. **Be told when the item you depend on changes.** A subscription over the
   owner's change envelopes for item B (lease acquired or released, evidence
   appended, item unblocked, effect accepted) delivered into the agent's
   inbox. Needs: subscription plus inbox. Wake mode: read-while-running.
3. **Continue work on another host or harness.** `work-link/v1` and
   `handoff/v1` exist; the successor is woken by a local command today. A
   `continue` message addressed to a host adapter on devbox starts a Codex or
   OpenCode successor that registers its own run naming `predecessor_run_id`.
   Needs: durable message plus a host adapter. Wake mode: start-after-stop.
   This is the proposed first milestone (section 7).
4. **Ask a peer of another model family for a review bound to the item.**
   The weekly-lanes plan (W2-4) wants one extra review pass on a flagged
   unit. A `review` request to a Codex principal, answered as an evidence
   item under the reviewer's own run. Needs: request message under section
   5.2's request mode. Wake mode: start-after-stop or read-while-running.
5. **Park on a quota limit and let another family find the parked claim.**
   Edge phase E4 parks and records. The directory and the owner's change
   envelope are how a peer of another family learns there is a parked claim.
   Nobody selects the peer; it finds the claim. Wake mode: read-while-running.
6. **Escalate `input-required` to an attended session, if one exists.** A
   non-interactive run that needs an answer transitions its task, releases
   its lease, and the question sits in the inbox of the owning principal's
   attended sessions. If none is attended, it stays parked; no component
   wakes a human. Wake mode: read-while-running.

Not enabled, by decision: selection of a recipient by the hub, scheduling,
retry, supervision, model routing, or any work-state change that bypasses
the owner's served operations.

## 5. Structure

### 5.1 Objects

**Registration.** A session registers through the existing `session.binding`
hook and keeps a liveness lease with `work.lease` semantics (heartbeat,
expiry, no kill). The record carries principal, harness and version, host
class, interactivity class, accepted request kinds, delivery channels (which
adapter can reach it and in which wake modes), bound repositories, and the
run and item it currently holds per the owner.

**Directory entry.** A rebuildable projection of registrations that
represents uncertainty explicitly. Five independent facts, each with its
evidence source and expiry, each defaulting to `unknown`:

| Fact | Evidence | Expires |
|---|---|---|
| registered | a registration row exists | never; it is history |
| observed | last heartbeat timestamp | after one lease TTL, then `stale` |
| reachable | the named adapter's last successful delivery or probe, per channel | per channel, after its own TTL |
| executing | the owner reports a live lease for this principal and run | when the owner's lease ends |
| attended | an interactive gateway session for this principal with a request in the last N minutes | after N minutes, then `unknown`, never `false` by inference |

A consumer of the directory sees freshness and stale or unavailable state
rather than a boolean, as the acceptance-explanation and observable-resource
contracts already require.

**Message.** An append-only record: message ID (sender-minted, the
idempotency key), producer principal (stamped by the hub on ingest, not
claimed by the sender), recipient principal or registration, kind (`notify`,
`request`, `continue`, `answer`, `cancel`), mode (section 5.2), a required
`context` naming a sprintctl item or run, bounded parts (64 KiB, the edge's
limit), evidence references by digest, expiry, and a delivery state. No
transcript, no raw output, no credential. Section 5.3 defines the states.

**Subscription.** A registration's standing interest in the owner's change
envelopes for named resources, delivered into its inbox as `notify`
messages with the owner's event ID carried through for deduplication.

### 5.2 Authority: two modes, and who does what

A message never carries authority. It carries a claim about authority that
the receiver and the owner verify.

- **Request mode.** The receiver acts under its own principal and its own
  credentials, authorizes independently, and may refuse. The sender's
  authority is irrelevant to what the receiver may do. Use for `review`,
  `notify`, questions, and any case where the receiver already holds the
  right to do the thing.
- **Delegation mode.** The receiver acts on the sender's behalf. Effective
  authority is the intersection of the sender's and the receiver's, carried
  as a chain (sender principal and run, receiver principal, authorities
  requested, expiry, depth), and the owner verifies the chain at run
  registration and at every lease and effect operation. Depth is capped at
  two. The packet carries no claim token and no provider credential; the
  receiver obtains its own through cred-broker under its own principal.

"Receiver's own run" alone is not a boundary: a receiver with broader
authority than the sender could do more than the sender could have. The mode
field makes the difference explicit and the owner enforces it.

Who performs the functions TS-1 and TS-2 reserve from Vuoro:

| Function | Performed by | Never by |
|---|---|---|
| select a recipient | the sending agent, from the directory | the hub |
| start a run | the host adapter or the provider's launch API, under the receiving principal | the hub |
| retry a failed run | the sending agent, by sending again, or the host adapter's own policy | the hub |
| choose a replacement model | the harness or the sending agent | the hub |
| expire a message | the sender's expiry on the message | the hub does not extend it |
| supervise | nobody; a liveness lease expires and the directory says `stale` | the hub |

If a later proposal wants the hub to perform any row, that is a target-state
change and is not this proposal.

### 5.3 Delivery semantics and required failure histories

Delivery is at least once. The states are `stored`, `delivered` (an adapter
handed it to a running session or a host adapter accepted it),
`acknowledged` (the receiver recorded it, by message ID, under its own run),
`expired`, `canceled`, `undeliverable` (the recipient registration was
`stale` for the message's whole life). Acknowledgement is separate from any
effect the receiver performs.

Histories the conformance suite must contain before the first milestone is
accepted, in the form `integration-topology.md` uses:

- A message is delivered twice; the receiver's second acknowledgement is a
  no-op and no second run is registered.
- The receiver performs an owner-side effect, crashes before acknowledging,
  and the message is redelivered: the owner's idempotency ledger, keyed by
  the message ID, reports the effect as already applied; the receiver
  acknowledges without repeating it.
- The receiver registers a run, crashes, and a different receiver of the
  same principal picks the message up: the run registry refuses a second run
  for the same message ID and the second receiver resumes or acknowledges.
- The sender cancels after delivery: the receiver sees `canceled` at its next
  poll; an effect already proposed is not withdrawn by the hub.
- The recipient registration goes `stale` during the message's life; the
  message becomes `undeliverable` and the sender's inbox receives the state
  change.
- Two senders address the same `continue` to two hosts: the owner's one
  lease per item decides; the loser's message ends `acknowledged` with a
  refusal recorded.
- The hub restarts with messages `delivered` but not `acknowledged`: they
  are redelivered, and the first history applies.

An existing Postgres outbox in vuoro-cloud is infrastructure these can be
built on. It is not evidence that any of them hold.

### 5.4 Interactive versus non-interactive

| Property | Interactive (attended) | Non-interactive |
|---|---|---|
| `attended` fact | evidenced, with expiry, per section 5.1 | `false` by registration |
| may receive `input-required` | yes | no; the task transitions, the lease releases, the question parks |
| may block waiting | never; every operation returns a handle | never |
| liveness | the gateway session | a liveness lease |
| terminal reason | optional | required, from the shared vocabulary (`completed`, `process-exit`, `start-failed`, `cancelled`, `timeout`, `usage-limit`, `crash-inferred`) |
| may send `continue` or delegation to | non-interactive and interactive | non-interactive only, depth-capped |
| may approve step-up | when S7 lands | never |
| credential path | gateway OAuth | cred-broker capability |

### 5.5 Three wake modes, answered per runtime

A message store cannot wake a stopped session. Outbound reach means a
running session can poll. Starting a successor needs a persistent poller, a
scheduler, or a provider launch API. The three must be answered separately.

| Runtime | Read while running | Attention during a run | Start after stop |
|---|---|---|---|
| Claude Code cloud, Cowork, claude.ai | poll the edge's `receive_messages` tool | none from the hub; the provider's session bus can message a running session (Claude only, provider-owned) | provider session create or scheduled trigger, through an adapter in vuoro.cloud; or a Routine that polls |
| Claude Code, workstation | poll, or a hook that polls at `UserPromptSubmit` | hook injection at the next prompt | host adapter over ACP (`claude-agent-acp`) |
| devbox sessions | host adapter polls and injects at the next turn | ACP prompt injection | host adapter over ACP (`codex-acp`, `opencode acp`) or `opencode serve` session create |
| Codex cloud | unverified (section 3) | none known | none known from the hub; Linear or GitHub mentions start tasks, which is not an adapter |
| Managed Agents | the `vuoro-worker` internal tools | the poller's preflight | the poller receives the next task from the vendor queue |

The host adapter is the component the first milestone proves. It is a small
poller holding a cred-broker session, heartbeating its registration, reading
its inbox, and driving a harness over ACP. It belongs in agentops beside
`handoff.py`, not in `vuoro-client` (transport-only) and not in Vuoro, and it
is where retry and model choice live if anywhere.

### 5.6 Placement by repository

| Repository | Owns |
|---|---|
| vuoro-cloud | the hub: registrations, directory projection, message store, subscriptions, delivery adapters, the new authorities in its OAuth scope map |
| vuoro (this repository) | contract text and JSON schemas (registration, directory entry, message, delegation chain); the provider-neutral conformance suite with the histories in 5.3, beside the lease and ledger suites; the edge tool rows; `vuoro-worker` internal tools |
| sprintctl | verification of the delegation chain at run registration and lease or effect operations, and the message ID as an idempotency key, if the owner agrees; work state changes only through its existing operations |
| cred-broker | one capability (`agent.mailbox`) and its receipt kind, through the reuse/gap matrix the control-plane plan prescribes (CRED-000) |
| agentops | the host adapter; the hooks that register, heartbeat and poll; migration of the `handoff/v1` wake |
| appservice | deployment; outside this note |

### 5.7 A2A: an internal contract, not an endpoint

A2A does not require a mesh; its clients poll server-held tasks. So a hub is
compatible with it. But A2A supplies a task state machine, message and part
shapes, and an agent card. It does not supply a worker inbox, delivery
acknowledgement, subscriptions over owner state, or an execution-ownership
contract. Those are this proposal's own.

Two distinct commitments: internal messages with A2A-shaped envelopes, and
an interoperable A2A endpoint. This note commits to the first only. Adopting
the shapes costs nothing and keeps the second open. The second has a
greppable waking condition: a repository outside the estate declares an A2A
client dependency on the hub. Until then the rebuild's "watch ACP, not A2A"
stands unreversed, and ACP is what the host adapter speaks.

## 6. Build or buy

| Option | Identity fit | Reach | Boundary | Verdict |
|---|---|---|---|---|
| A. A2A SDK server runtime in vuoro.cloud | its security schemes mapped onto gateway assertions | hub | the SDK's task store becomes a second store; no inbox, ack or subscription semantics | defer to the 5.7 waking condition |
| B. In-built registrations, message store and subscriptions in vuoro.cloud, A2A-shaped envelopes, Postgres outbox | native: gateway assertions and cred-broker principals as they are | hub | holds no work state; owner verifies chains and idempotency | **recommended** |
| C. Provider-native only: Anthropic session bus, Managed Agents queue, OpenCode serve | three identity systems | Claude to Claude, or LAN | no shared record of who asked whom, no cross-harness directory | adapters under B, never the record |
| D. Generic bus (NATS, Redis streams) | none; identity bolted on | hub | a new component that speaks no principal | rejected at this scale |
| E. Hosted agent-registry product | external | external | the inventory leaves the estate | rejected |

## 7. Sequence

**Gate 0, the record.** A register row for the hub and one for the host
adapter, each with a waking condition written as an artifact test. A note in
the rebuild plan that A2A shapes are adopted internally with no endpoint.
The `vuoro-cloud` disposition per section 9.

**M1, directory.** Registration from the existing `session-binding` hook plus
a liveness lease; `list_agents` and `describe_agent` on the edge, returning
the five facts of 5.1 with freshness; the host adapter heartbeating from
wherever section 9's check puts it. Conformance: an expired lease reads
`stale`, never disappears; `attended` is never inferred `false`; a card
carries no credential and no transcript reference; a broader authority is
refused.

**M2, one demonstrated continuation.** Durable messages, subscriptions, and
the host adapter. The acceptance is a single recorded run: a Cowork session
makes a work link and sends `continue` to a devbox or workstation principal;
the host adapter starts a Codex or OpenCode successor over ACP; the successor
registers its run naming the predecessor; evidence lands under the successor's
run; and the duplicate-delivery and crash-after-effect histories of 5.3 are
exercised against the real owner and recorded. This replaces the `handoff/v1`
ssh wake for that path. Request mode only; no delegation.

**Separate decision: dispatch.** Delegation mode, the chain verification in
the owner, and the depth cap. It is a distinct proposal because it touches
the owner's run registration and because the TS-1 table in 5.2 must be
re-confirmed against agentops with the real text in hand.

**Later, each on its own waking condition:** provider push adapters, signed
directory entries, an interoperable A2A endpoint.

## 8. Risks and falsifiers

- **Shadow work queue.** Every message names an item or run; refused
  otherwise. Falsifier: a `stored` message older than one lease TTL whose
  context names no live owner record.
- **Coordinator by accretion.** The function table in 5.2 is closed. An
  operation that selects, starts, retries or routes reopens the
  `meta-layer` row's waking condition and needs a target-state change.
- **Injection through messages.** Content is data with a stamped producer.
  The control is reach and credentials (default-deny egress, no credential in
  a packet), not classification.
- **Replay and forgery.** One-use `jti` and edge proofs on the interactive
  path; the hub stamps the producer; message IDs are idempotency keys at the
  owner.
- **Attended by assumption.** The single most likely misuse: treating an
  interactive registration as a human present. The directory's `attended`
  fact expires and reads `unknown`.
- **Expiring sessions.** Every operation is non-blocking.
- **Two identities per host.** A gateway subject and a cred-broker principal
  for the same host bind to one principal string in the registration, or the
  directory lies.
- **Cost.** Every poll is a tool call. Use `ttlMs`; account for Routines
  starting without connectors.

## 9. Decisions and checks

Checks to perform, not to decide:

1. From devbox as `agent`, one HTTPS request to the vuoro.cloud gateway,
   recorded with the OPNsense rule that allowed or denied it. This places
   the host adapter.
2. From one Codex cloud environment with the allowlist set to the gateway
   host, one request against the edge's liveness probe. This fills the
   section 3 row.

Decisions, with recommendations:

1. **Where M1 and M2 are served.** Recommendation: vuoro.cloud, on the edge
   decision's blast-radius argument, and record this as the work that wakes
   the `vuoro-cloud` row alongside its `served_profile` condition rather than
   waiting on that condition. The row's hold is about tenancy and onboarding;
   M1 and M2 add neither.
2. **A2A posture.** Recommendation: internal shapes, no endpoint, the
   rebuild's verdict unreversed, with the 5.7 waking condition recorded.
3. **Principals.** Recommendation: M1 and M2 run under host principals
   (`issuer:subject:epoch`) as today. Request mode needs nothing more.
   Per-run principals (S7) become necessary only with delegation mode, so
   they are part of the dispatch decision, not this one, and D2 stands
   meanwhile.
4. **Register rows.** Recommendation: add the two rows at Gate 0 as `open`,
   with M2's recorded run as the artifact test that moves them to `active`.

## Sources outside the repository

Checked by web search on 2026-10-14, not against primary sources:
[A2A specification](https://a2a-protocol.org/latest/specification/) (clients
poll server-held tasks; agent card; extensions),
[ACP registry](https://zed.dev/blog/acp-registry) and
[OpenHands ACP agents](https://docs.openhands.dev/openhands/usage/agent-canvas/acp-agents)
(adapters for Claude Code, Codex and OpenCode),
[Codex agent internet access](https://developers.openai.com/codex/cloud/agent-internet)
(agent phase off by default, configurable per environment with a domain
allowlist), [Codex changelog](https://developers.openai.com/codex/changelog/)
(`codex mcp-server` removed; MCP client retained),
[OpenCode server](https://opencode.ai/docs/server/) (`opencode serve`,
session API, basic auth).
