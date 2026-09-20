# Reachability as a Control Question

**Status:** Draft. Not published anywhere; carries draft front matter in kotona.app so the site build excludes it (DECISION 2, operator, 2026-09-20).
**Written:** 2026-09-19
**Landed:** 2026-09-20, from the session that picked up the extended realignment. Previously existed only in `~/Downloads`.


2026-09-19 · @Someone

When agent work executes outside a perimeter you control, the record goes partial without raising an error. Notes from a personal system, offered for discussion rather than as practice.

## A gap that raises no error

When an automated worker executes inside your perimeter, recording what it did is a design choice. When it executes outside, recording what it did is an integration problem — and if you have not solved it, the record simply stops covering that work.

The failure mode is worth sitting with, because it is unusually quiet. Nothing errors. The workers that run inside keep writing their events. Dashboards look normal. What changes is that the proportion of activity covered by the record falls, gradually, as more execution moves outside. You find out when you go looking for something that should be there and is not.

This is not a hypothetical about the future. Agent execution is already distributed across places with different owners: a scheduled run in a vendor's cloud, a session started from a phone, a task triggered by a repository event. Each of those has its own sandbox, its own network policy, and no route to an internal database.

**The control question is therefore not "can we trust the agent".** It is: *for what proportion of automated activity can we reconstruct what was done, by what, and on whose authority?* That question has a number as its answer, the number is currently unknown in most setups, and it is almost certainly below one.

I have been working through this on a personal system — a homelab, one operator, no production consequences. The setting is small enough that the mechanisms are visible and cheap enough that I can be wrong without cost. What follows is what the problem looked like from there. It is offered as a set of observations and open questions; nothing here is a claim about how anything should be done at work, and nothing here describes any internal system.

## The problem shape is not new

The useful realisation is that this is not a novel AI governance problem. It is processing performed outside your perimeter by a party you do not control, and there is a long-established body of practice for that.

The familiar questions transfer almost unchanged:

- What activity is being performed outside, and can you enumerate it?
- What can the outside party reach, and what is it prevented from reaching?
- What evidence is produced, who produces it, and can it be shown to be unaltered?
- What happens when the outside party becomes unavailable mid-operation?
- Who authorised the activity, and is that authorisation revocable and time-bound?

None of those requires a new framework to ask. What is new is only that the outside party is a model runtime rather than a service provider, that engagements last minutes rather than years, and that the volume makes case-by-case review impossible.

That last point is the one that actually bites. Review capacity does not scale with automated throughput. So the control shifts from reviewing each output to constraining what any output *can be* — which is a shift from detective to preventive, and it is a shift most people make implicitly without writing it down.

**Two framings I found more useful than the AI-specific ones.**

The first is the assurance case: a top claim, an argument, and the evidence supporting it. Writing one for a single automated worker takes half a page and reliably finds a hole. The discipline is in being forced to say *why* you believe a control works and *what* would demonstrate it.

The second is segregation of duties, applied literally. The identity that produces change must not be the identity that approves or applies it. This sounds obvious and is routinely violated in automated setups, because an automated actor running under a human's credentials can approve its own work and nothing in the tooling objects.

Neither of these is an AI control. Both do more work here than anything written specifically for AI, and I think that is the more interesting observation: the terminology in this space is new, and most of the load-bearing practice is not.

## Proposal and reconciliation

The separation that does the work is simple to state: **a worker outside the perimeter may read, may take an exclusive assignment, may record what it did, and may propose a change. It may not apply one, and it holds no credential that could.**

Something inside the perimeter — already trusted, already holding the credentials — picks up the proposal and applies it, through whatever change process already governs that system. The proposal carries the assignment it was produced under and a reference to the run that produced it.

| Crosses the boundary | Never crosses |
| --- | --- |
| What work exists and its acceptance criteria | Credentials for target systems |
| An exclusive, time-limited assignment | Authority to approve or apply |
| Evidence of what was done | Direct access to production state |
| A described change, not an applied one | Anything unreviewable by construction |

Three properties follow, and they are the reason I think this shape is worth the trouble.

**It is segregation of duties, implemented rather than asserted.** The producing identity is technically incapable of approving or applying. That is a stronger statement than a policy saying it must not.

**Duplicate work becomes visible before it happens.** Because a proposal names the resources it intends to touch before anything executes, a repeated proposal from a restarted worker is detectable in advance. Anyone who has watched an automated process cheerfully repeat an expensive operation after losing its state will recognise the value.

**Unavailability is handled by design rather than by exception.** Assignments are leased with an expiry, not locked. A worker that vanishes — and they do vanish; sandboxes expire, sessions time out waiting for approvals — releases its assignment automatically. An assignment that cannot expire is a deadlock waiting for a person to notice.

That last one is the single most under-implemented control I found when surveying the available tooling. Nearly everything has an atomic assignment. Almost nothing has an expiry. In a setting where the worker runs somewhere you cannot inspect or terminate, expiry is not hygiene — it is the only recovery mechanism you have.

## What can and cannot be evidenced

This is where I would most want to be corrected, because the temptation to overclaim is strong and the industry is currently obliging it.

**What can be evidenced today.** A change was made by a distinct automated identity, under its own signing key. It was built by a specific process from a specific commit, and that is cryptographically attestable through established supply-chain tooling. It was applied only after checks that the producing identity could not bypass. The record of what happened is append-only and, with hash chaining, detectably unaltered after the fact.

That is a real chain and it is verifiable end to end.

**What cannot be evidenced today.** Which model produced the change. Which instructions it was operating under. Which plan it was following. What it considered and rejected. There is no standard for attesting any of that, and the sketches circulating are sketches. Attestation chains begin at the commit, so everything upstream of the commit is outside them.

The honest formulation is therefore narrower than people want: *we can prove what was produced and by which identity, and we can record — but not cryptographically prove — what produced it.*

**A recorded run manifest is the middle ground.** A record naming the runtime, the model, the configuration revision and the assignment, bound to the change, gives you reconstruction without attestation. It answers "what was this made with" as a matter of record rather than proof. That distinction matters in any setting where the difference between a log entry and an attestation is understood, which is most of the settings that would care.

**Why it matters that this is stated precisely.** A control description that says "AI-generated changes are cryptographically attested" is not true of any system I am aware of, and the gap between that sentence and reality is exactly the sort of thing that gets found later by someone whose job is finding it. "Recorded and reconstructable, not attested" is less satisfying and defensible.

One more limit worth naming: no deployed product does prompt-injection prevention as an enforcement boundary. Everything marketed that way resolves to classification and heuristics. Useful as monitoring, not usable as a control you would rely on. The controls that work are the boring ones — constrain what the worker can reach, and assume manipulation succeeds.

## Proportionality

The test I have been applying: adopt the smallest version of an established practice, and skip anything whose smallest version is still a platform.

| Control | Cost | Verdict at small scale |
| --- | --- | --- |
| Distinct automated identity, own signing key | Hours | Load-bearing. Nothing else works without it |
| Assignment expiry with heartbeat | Hours | Load-bearing once work runs outside the perimeter |
| Append-only record, hash-chained | Hours | Load-bearing. Cheapest control in this list |
| Default-deny network egress, short allowlist | A day | Load-bearing. The single largest risk reduction available |
| Change applied only through the existing review path | Free if it exists | Load-bearing |
| Run manifest recorded per execution | Days | Worth it. It is what makes anything comparable later |
| Workload identity infrastructure | Weeks, ongoing | Disproportionate. Platform-native identity gets most of the property |
| Policy engine for semantic intent | Weeks, immature | Not yet. The technology is early and the claims outrun it |
| Formal certification | Substantial | Meaningless without an organisation behind it |

The pattern in that table is that the cheap controls are the effective ones, and the expensive ones mostly buy assurance about things the cheap ones already prevented.

**One artifact I would keep regardless of scale: the inventory.** A register of every automated worker, its purpose, what it can reach, who owns it, and how it fails. At personal scale this is one file. Writing it is where you discover that something holds access it does not need — which is the finding that justifies the exercise, every time I have done it.

**One habit I would drop: adopting controls because a framework names them.** Several of the AI-specific frameworks are useful as decomposition and poor as control lists. The inventory idea is worth taking from one of them; the certification apparatus around it is not, at any scale below an organisation.

**The proportionality question I keep returning to** is whether a control's smallest honest version still produces the property it claims. Hash chaining at small scale genuinely detects tampering. An assurance case at small scale genuinely finds holes. A certification at small scale produces a document. That seems like a usable test, and I would be interested in where it breaks.

## What transfers, and what I am unsure about

Setting out the limits first: this comes from a single-operator hobby system with no production consequences, no second reviewer, and no external validation. The mechanisms are visible precisely because the stakes are not. That makes it a useful place to see shapes clearly and a poor place to test whether they survive contact with scale, procurement, or an audit.

**What I think transfers.**

- The control question itself — what proportion of automated activity is reconstructable — is worth asking anywhere, and the answer is usually unknown.
- The producing identity must be technically incapable of approving or applying. This is ordinary segregation of duties and it is routinely violated by automation running under a person's credentials.
- Expiry on exclusive assignments, once work executes where you cannot terminate it.
- Stating the evidence limit precisely: recorded and reconstructable is not the same as attested, and the difference matters to exactly the people who will ask.
- The proportionality test — does the smallest honest version of this control still produce the property?

**What I doubt transfers.**

- The specific boundary placement. Mine is drawn where it is because I have no compliance obligations and a single blast radius to protect. A setting with data residency, separation obligations or third-party requirements would draw it differently, possibly much earlier.
- The build-it-yourself posture. At one operator, owning the record is cheap and the market is unstable enough to justify it. At organisational scale, that reasoning inverts.
- Any assumption that quiet failure is acceptable while the mechanism is proven out.

**Open questions I would genuinely like views on.**

1. Is "proportion of automated activity that is reconstructable" a measurable control objective, or does it collapse under definition as soon as you try to operationalise it?
2. Where should the boundary sit when the outside runtime is a contracted service rather than a consumer subscription — does a contract change the containment answer, or only the liability answer?
3. Is a recorded run manifest sufficient for reconstruction requirements in practice, or does anything serious require attestation that does not currently exist?
4. Does the review-capacity argument — replace volume of review with narrowness of permission — hold where review is an obligation rather than a preference?

I do not have positions on these. The reason for writing this up is that the shapes seemed clear enough to be worth discussing, and the setting is deliberately one where being wrong costs nothing.
