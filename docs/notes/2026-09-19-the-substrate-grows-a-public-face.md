# The Substrate Grows a Public Face

**Status:** Draft. Not published anywhere; carries draft front matter in kotona.app so the site build excludes it (DECISION 2, operator, 2026-09-20).
**Written:** 2026-09-19
**Landed:** 2026-09-20, from the session that picked up the extended realignment. Previously existed only in `~/Downloads`.


2026-09-19 · @Someone

Notes on why a personal agent-coordination substrate ends up needing a public endpoint, and what that costs. Written for people running something similar.

## The work moved and the substrate stayed

If you built tooling to coordinate agent work, you probably built it where the agents were: on your own machines, against a local database, driven by CLIs. That was correct in 2024 and it is quietly wrong now.

The agents moved. A meaningful share of sessions now run in a vendor's cloud — a scheduled task firing at 06:00, a session started from a phone, a cloud-hosted run triggered by a pull request. Those sessions cannot see your database. They are not on your network, they never will be, and no amount of Tailscale fixes it, because the sandbox egress policy is not yours to set.

The result is a hole rather than a limitation. Work happens; no claim is taken, no run is recorded, no evidence is written. Your audit trail is complete for the sessions you happen to run locally and silent for the rest. If you built the substrate to answer "what did my agents do and why", it now answers that question for a shrinking fraction of the work.

This is the uncomfortable part: the hole does not announce itself. Local sessions keep working. The queue keeps looking healthy. The record degrades from complete to partial without a single error, and you only notice when you go looking for something that should be there.

I think the response is straightforward but unobvious, and it is the thing this note is about: **the substrate needs a public face** — not to serve other people, but to be reachable from runtimes you do not host.

## The market solved the other half

2026 produced a lot of tooling in this space and most of it solves the half you already have. Work queues, dependency graphs, worktree-per-task, spec artifacts — these converged, and they converged well. What did not converge is anything about the *record*.

Three gaps show up consistently across the tools I looked at.

**Claims without expiry.** Nearly every tool has an atomic claim. Almost none has a lease. A crashed agent holds work forever, and in a world where sessions run in sandboxes that expire mid-wait, that stops being an edge case. Expiry is a correctness property, not an implementation detail.

**No run-level provenance.** Tools record that a task was done. Very few record what it was done *with* — which model, which profile, which prompt revision, which harness build. Without that, you cannot compare two setups, you cannot reproduce a result, and you cannot answer why a regression appeared in March.

**Configuration is never evaluated.** Every harness ships a way to distribute agent definitions. None of them has a notion of a definition being *admitted on evidence*. People change their system prompts and subagent topologies constantly, on vibes, and the tooling to measure whether it helped now exists and is cheap. Almost nobody runs it.

There is also a mortality problem worth naming. Inside nine months, one hosted orchestration product shut down, another's vendor abandoned it, a coordination server was discontinued by its author, and a memory platform withdrew its self-hostable edition. If you are a single operator, the lesson is not "build everything" — it is that the *state* must be yours, self-hosted and permissively licensed, even when the execution is rented.

Which is, conveniently, the same conclusion the reachability problem pushes you toward. You are going to keep owning the record. The only question is whether the record can be written from where the work now happens.

## Publishing the substrate

The move is to expose your coordination substrate as an MCP server on a public HTTPS endpoint, and register it once as a custom connector. Three things then fall out that I did not expect when I started.

**One registration reaches everything.** In Anthropic's products, connector calls are made server-side by their infrastructure, not from the agent's sandbox. Tokens never enter the sandbox and the sandbox egress allowlist does not apply to MCP at all. So a single registration reaches chat, desktop, mobile, the cloud coding sessions and the scheduled ones. You are not integrating five times, and the sandbox networking regressions that periodically break `curl`-from-inside are simply not on this path.

**The protocol wants your object model.** The July 2026 MCP revision removed sessions at every layer. Cross-call state is now carried by server-minted opaque handles passed as ordinary tool arguments — create a thing, get an id, pass the id to subsequent calls. A leased claim is exactly that handle. The security rule the spec states for handles is the one you want anyway: possession is not authorization, validate the handle against the auth context on every call, make them unguessable.

This is a genuinely pleasant convergence. The thing the protocol made mandatory for its own reasons is the thing a careful coordination layer already had.

**Statelessness stops being a compromise.** With protocol sessions gone, any instance serves any request. No sticky sessions, no shared session store. Your state lives in your own database keyed by handle, which is where it belonged.

### What the surface actually is

Small. Read the ready work, describe one, claim it under a lease, heartbeat, append evidence, write a session note, propose a change, complete. Eight tools, and every one of them classifiable as read, coordinate, record, or propose.

That classification is the whole design. If a tool cannot be put in one of those four buckets, it does not belong on the public surface — which is the next section.

## The boundary

Intent, coordination and evidence may cross into a runtime you do not control. Effects and credentials may not. A hosted session's maximum achievable outcome is an unmergeable branch and a queued proposal.

The reason this is defensible rather than merely cautious is that it is a shape you already trust. If you run GitOps, you already accept that an agent with a worktree and no cluster credential is safe, because the reconciler verifies the commit signature and the bot identity cannot merge. Containment comes from the mechanism, not from the agent's good behaviour.

The cloud case changes one thing: the agent is now somewhere you cannot inspect. That degrades the *attestation* story, not the *containment* story. Worth being precise about which one you lost.

**So effects are proposed, not applied.** A session emits an intent — a described change with a named resource set, carrying the lease it was produced under and the run it came from. Something on your own side, already trusted and already holding the credentials, picks it up and executes. Diff-shaped intents are the overwhelming majority and go through the pipeline you already have. Declarative changes become commits, so they are diff-shaped by the time anything reconciles them.

Two things fall out of this that I did not design for and would not give up.

**Duplicate detection becomes free.** Because an intent names its resource set before execution, a re-run session's second identical intent is visible as a duplicate *before* anything happens. If you have ever watched an agent cheerfully re-run an expensive infrastructure command because its context was reset, that is the fix, and it only works because the intent is declared rather than executed.

**The signing story gets honest.** The reconciler signs, not the cloud session. There is no way to cryptographically attest an unattested runtime's authorship, and the industry does not have a standard for it — attestation chains start at the commit, so which model, which prompt and which plan are all invisible. What you can have is a verifiable chain from a signed commit back to a run record naming the runtime, the model and the profile revision. That is weaker than authorship and stronger than what is otherwise available. Describe it in exactly those terms and resist the temptation to round up.

## What it costs, including the parts that disappointed

**You are now running an internet-facing, credential-holding service with one user and no on-call.** That is a change in kind. You cannot IP-allowlist it, because the callers are vendor clouds with shifting egress. You cannot use a self-signed certificate. The credential lives in a vendor's connector configuration, and at least on one platform the auth settings are immutable after registration — changing them means removing and re-adding.

The controls that were good practice locally become load-bearing: hash-chained evidence so tampering with the record is detectable, lease expiry so a stale runtime cannot replay a completion, audience-validated tokens so a credential cannot be used against a different server, rate limiting, and actual endpoint monitoring, because it is now the only way you learn something is wrong.

What is genuinely at risk after all that is queue integrity: poisoned work items, false completions, evidence entries that lie. Not exfiltration of anything that matters, provided you held the boundary.

**There is an escape hatch, with a coverage cost.** At least one vendor offers a worker model where you run a poller on your own host that executes tools locally, wrapping an internal MCP server as custom tools — no public endpoint at all. It is strictly safer. It also reaches only that vendor's API surfaces, not the chat clients or the phone. If the value you want is "start something from my phone and have it land in the record", you need the public endpoint. If the value is unattended scheduled work, prefer the worker.

### The part that came out weaker

I expected the strongest argument to be quota routing across billing pools — different runtimes bill from different allowances, so dispatch to whichever has headroom. It does not work, because **there is no supported programmatic read of individual plan consumption on either major vendor.** The interactive usage screens exist. The telemetry export has no quota gauge. The aggregation APIs are organization-only or enterprise-only. The endpoint the UI calls is not for third-party use and rate-limits accordingly.

What survives is reactive: a rate-limit event tells you which limit you hit and when it resets, so you can release the lease, park the claim and re-dispatch on a different family. That is worth having — it turns a lost afternoon into an event in the record — but it is failover, not balancing. Predictive routing is not buildable on published interfaces today, and I would rather say that plainly than describe the thing I wanted to be true.

The second-best is self-instrumentation. Your server is the one vantage point spanning every runtime, so reconstructing approximate consumption from your own call log is possible. It is worse than an API and it is what exists.

## The pattern underneath

This is the second time I have done the same thing without noticing it was the same thing.

The first was a personal site. I started publishing machine-readable surfaces on it — a prompt at the top of each note aimed at a reader's agent rather than the reader, a build-time graph projection emitting an index of articles and the edges between them. The intent was never SEO or a chatbot. It was that the site had readers who were not people, and it had no surface for them.

The substrate is the same move. It has users that are not people, running in places I do not control, and it had no surface for them either.

I think this is a general shape and it is worth naming: **personal infrastructure grows an agent-facing surface, and the surface is a different shape than the human one.** Not a nicer API. A different set of affordances — stable identifiers, declared durability, explicit handles, machine-checkable acceptance criteria, and a record that assumes the caller will vanish mid-operation.

Two things follow that I would not have predicted.

**The agent-facing surface is more disciplined than the human one.** It has to be. A human tolerates an ambiguous status field; a stateless protocol does not. Publishing forced me to say what a claim's lifetime is, what a completion means, and which operations are safe to call twice — questions the local version let me leave vague for two years.

**Reachability is an architectural property, not an operational one.** I had been treating "where does this run" as deployment detail. It is not. It determines which runtimes can participate, which determines what the record contains, which determines what questions the system can answer. That is an architecture question wearing an ops costume.

The honest limit of all this: I am one person, with no external users, and nothing here has been validated by anyone's production. Treat it as a design note from a hobby corpus rather than a recommendation. The parts I would defend hardest are the ones that cost almost nothing — lease expiry, chained evidence, a four-bucket test for what may be published — and the part I would treat most sceptically is my own enthusiasm for reaching more runtimes than I have a demonstrated need for.
