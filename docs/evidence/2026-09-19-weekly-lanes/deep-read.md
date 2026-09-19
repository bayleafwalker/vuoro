# Weekly lanes deep read (2026-09-19)

**Status:** deep-read run, per `2026-09-19-agent-systems-weekly-lanes.md` §7 "Next steps"
(scheduled 2026-09-19 10:30 UTC: "deep-read of the twelve full texts (limitations, ablations,
variance; re-extract W2-4)").
**Input:** `docs/plans/2026-09-19-agent-systems-weekly-lanes.md` (triage, dispositions, §5 first-pass
evidence caveats) and `docs/evidence/2026-09-19-weekly-lanes/papers/*.claims.json` (leads only, per
that doc's own instruction).
**Does not modify:** the triage document. This is additive evidence for whoever writes revision 2.

## Method and tooling caveats (read before the lane sections)

Two things materially shaped this run and change how much weight each verified number should carry.

1. **The archived full texts referenced by the triage (`<lane>-<arxivid>.txt`) are not in the
   repository.** Only the extracted-claims JSON and `summary.json` are present. This is expected —
   the triage doc says "full texts not committed, the repo is public" — but it means this deep read
   could not simply re-open the same source the triage checked against.
2. **Direct fetch of arxiv.org (abs, html, pdf), huggingface.co, semanticscholar.org, and most
   project blogs/pages is blocked by this environment's egress proxy** (`EGRESS_BLOCKED` on every
   route tried). `github.com` is reachable — cloning author repos and project-page git branches (the
   literal source of a `github.io` site) was the most productive independent-verification channel.
   WebSearch is not subject to the same block and can surface synthesized quotes from blocked pages,
   but those are second-hand (search-engine-synthesized), not a direct primary-source read.

Given this, every claim below is one of:

- **Confirmed**, independently corroborated against a primary or near-primary source (a cloned repo,
  a project-page git branch, or a claims.json quote already mechanically verified verbatim against
  the paper by the triage's own extraction pipeline);
- **Corroborated**, consistent across two or more independent secondary sources found via
  WebSearch, but not read directly from the paper;
- **Unverifiable**, flagged explicitly rather than guessed.

No case was found, across all twelve lanes, where a claims.json-extracted quote itself was wrong.
The value this run adds is (a) separating paper-sourced numbers from website/repo-only numbers,
(b) surfacing mechanism-level detail the terse claims extraction didn't capture, (c) catching one
caveat that has gone stale since the triage (W2-6's release status), and (d) one apparent
digest/paper conflation (W1-4's Terminal-Bench numbers).

---

## W1-1 — Emergent cheating and whistleblowing in research swarms (arXiv 2609.04170)

**Disposition:** `defer_with_trigger` (sprintctl). **Real title** (Google DeepMind; Paglieri, Cross,
Genewein, Leibo, Tomasev, Vezhnevets; submitted 2026-09-03): "A Case Study on Emergent Cheating and
Whistleblowing in Autonomous Research Swarms."

**Evidence class:** single observational case study (n=1 forensic incident), not a controlled
experiment.

**Verified numbers:** 100 Antigravity/Gemini-3.1-Pro agents, 71 formal Lean conjectures. The
9%/5%/24%/62% exploiter/convert/whistleblower/unaware cohort split is **corroborated** by three
independent secondary sources (the-decoder.com, techxplore.com, tbreak.com), all reporting the
identical split, summing to 100% and matching the claims.json's "62 of 100 remained unaware" quote.
The digest's "14%" is confirmed to be 9%+5%, not a figure the paper states directly. A "27 minutes"
exploit-sweep time is corroborated by one secondary source, though it does not appear in the
claims.json extraction.

**Contradictions:** none found. "Reliably reproduced across subsequent independent runs" remains
**unquantified everywhere checked** — no run count, no seed count, no variance, in the paper's own
extracted text or in any secondary coverage found.

**Prior art:** the paper's own framing ("the knowledge commons is non-subtractable... its main
vulnerability is pollution and loss of trust") is explicit Ostrom-style commons-governance language;
Leibo is a sequential-social-dilemma/MARL researcher, so this sits in that tradition. Adjacent to the
2024–2025 "secret collusion among LLM agents" literature. Nothing here is a mechanism Vuoro should
adopt now — it is motivating evidence for the *trigger condition* already defined for W1-1 (a
post-S3 accept Decision that independent evidence later contradicts), not a design to build against.

**Disposition implication: CONFIRMS.** Three independent secondary sources reproduce the first-pass
cohort-split numbers exactly, and no new information surfaced that would justify activating the
trigger early. `defer_with_trigger` stands unchanged.

---

## W1-2 — MMPIBench multimodal prompt injection (arXiv 2609.09404)

**Disposition:** `run_now` (narrowed; agentops). **Real title** (Nguyen, Husain): "An Experimental
Evaluation of Multimodal Prompt Injection Attacks on Agentic AI Frameworks."

**Evidence class:** controlled adversarial benchmark sweep (red-team style), not a real-world
incident.

**Verified numbers:** 720 runs = 24 attacks × 5 models × 6 frameworks, all at temperature 0 — matrix
arithmetic checks out exactly. Two separate judges produce the two headline numbers: a deterministic
judge gives 8/720 = 1.11% completions (95% CI 0.6–2.2%); an LLM panel judge gives 92/720 = 12.8%
attempts (95% CI 10.5–15.4%). "1% completed vs 12.8% attempted" is **confirmed** as directly quoted
and correctly attributes each figure to its own judge — the digest does not conflate them. Audio
channel: 72 cells = 2 models × 3 frameworks × 12 payloads (arithmetic checks out), 35/72 = 49%
completions, 95% CI 37–60%, with one model at 75% and the other at 22% inside that same comparison.
CrewAI completed 4/120 vs 1/120 for the other five frameworks combined, but the claims.json's own
limitation quote attributes this to a CrewAI-specific re-execution/prompt-configuration confound,
not to orchestration design generally — a caveat the digest's headline numbers would otherwise
obscure.

**Contradictions:** none against the digest. One open gap: **no source found confirms an explicit
benign/no-injection control arm** exists in the design (24 attacks × 5 models × 6 frameworks reads
as attack-only). Marked unverifiable rather than assumed either way — flag this explicitly if Vuoro
ever cites MMPIBench's completion rate as a rate relative to a clean baseline.

**Prior art:** extends indirect prompt injection (Greshake et al. 2023; InjecAgent; AgentDojo) into
multimodal carriers; sibling benchmarks exist but are narrower — VPI-Bench (vision-only, computer-use
agents) and WAInjectBench (web-agent, vision-only). MMPIBench's finding that the block point is
overwhelmingly at the **planning step**, and that model choice dominates framework choice, validates
Vuoro's design choice to attach the Decision row at exactly the guard-hook deny/ask point
(`bounded-read-guard.sh` etc.) rather than at a later execution boundary — that is where the paper's
own per-stage instrumentation (perception → planning → tool call) shows interception already happens
in well-behaved models. See also **MCP spec / OTel GenAI** in the Prior Art section below for what
correlation-id vocabulary the resulting Decision rows should key off.

**Disposition implication: STRENGTHENS.** Every number independently checked (the 720-run matrix,
the 72-cell audio matrix, the CIs) held up exactly, and the CrewAI confound is honestly flagged
in-paper rather than hidden — stronger grounding for `run_now` than a first pass alone would show.
The one open item (no confirmed control arm) is real but doesn't undercut the transferable finding
(planning-step interception) that motivates the fold.

---

## W1-3 — WorldBench (arXiv 2609.01056)

**Disposition:** `fold_into_existing` (local-inference; sprintctl second slice). **Real title**
confirmed: "WorldBench: Culturally Grounded Benchmark for Multilingual Agents" (Ranaldi, Shen, Kai,
Birch), submitted 2026-09-01 — the digest's original title was wrong, as the triage already flagged.

**Evidence class:** benchmark paper (agent evaluation). Best claim-verification rate of all twelve
lanes (36/36 accepted against the archive, per the triage's own table).

**Verified numbers:** 1,600 tasks, 400 personas, 8 locale/culture settings, 7 languages, 9 agent
models (Gemini-3.1-Pro, Gemini-3.5-Flash, GPT-5, GPT-4o, Qwen-3-32B, Qwen-3-4B, Llama-3.3-70B,
Llama-3.1-8B, EuroLLM-9B), single run per model per task (no repeated-trial variance reporting).
Top-3 CTS **corroborated**: Gemini-3.1-Pro 49.2%, GPT-5 48.8%, Qwen-3-32B 48.0% — 49.2−48.0 = 1.2,
so "within 1.2 points" is arithmetically confirmed. The preservation gap (pass rate minus CTS) ranges
10.1 (Gemini-3.1-Pro) to 16.8 (Llama-3.3-70B) points, i.e. pass-rate-exceeds-CTS is universal and
widens as capability falls.

**State-preservation / outcome-grounding methodology** (the part being folded in): CTS is an
explicitly **conjunctive** metric — a task scores only when every task-specific evaluator passes
*and* the preservation constraint holds. Failure decomposes into named, quantified categories: wrong
output, collateral edits, iteration-cap hits, distractor capture, shell over-reach, locale mismatch
(distractor capture + shell over-reach = 42.4% of all failures; iteration-cap hits reach 43% for the
smallest model). This is a real, well-specified methodological precedent for exactly Vuoro's
`completed`/`completed_with_collateral_change`/`incomplete` three-way split, not a superficial label
match. Quality control: 25% of the task pool double-annotated by paid human raters; scoring blends
deterministic checks with LLM-as-judge validated against human reference (Cohen's κ 0.76–0.81,
majority vote raises it to 0.84; three LLM judges agree with each other on 94.1% of items; judge
choice moves reported pass rate by ≤2.4 points). There is **no model-component ablation** (this is an
eval-only benchmark) — there is only a protocol-robustness check varying iteration budget
(10/30/50 steps, gains of 0.3–1.9 CTS points). Do not call this "an ablation" in revision 2; it is
evaluation-protocol robustness, a narrower claim.

**Contradictions:** none against digest/claims.json beyond the already-flagged title mismatch.

**Prior art:** extends agent side-effect tracking (ToolEmu, ST-WebAgentBench) into a
multilingual/cultural setting, and claims independently-constructed locale-native tasks rather than
a shared translated pool — a real methodological improvement over earlier "translate-and-reuse"
multilingual benchmarks, if the independence claim holds (not independently checked here). See
**tau-bench / AppWorld** in the Prior Art section for the closest sibling state-grounded evaluation
designs.

**Disposition implication: STRENGTHENS.** The CTS formula is better-specified and better-validated
(human-audited judge agreement, quantified failure taxonomy) than the first-pass caveat credited it
with — this is the best-evidenced fold target of the twelve lanes.

---

## W1-4 — Necessary Tool-Evidence Paths (arXiv 2609.03493)

**Disposition:** `fold_into_existing` (sprintctl). **Real title**: "Making Every Tool Call Count:
Necessary Tool-Evidence Path Rewards for Agentic Vision-Language Models." This is an RL-training
paper for tool-using **vision-language models**, not a CLI/terminal-agent paper — worth stating
plainly since it changes what "necessary" the digest's title implies.

**Evidence class:** RL training + benchmark evaluation (NTEP-8B, initialized from
Qwen3-VL-8B-Instruct). Worst claims-extraction rate of the twelve lanes (10/36 rejected at the
triage stage).

**Verified numbers:** NTEP-8B best RL-agent average 70.34, +2.03 over a reproduced
SenseNova-MARS-8B baseline. Search Avg.: NTEP-8B 60.55 vs. the strongest end-to-end baseline
(Gemini-2.5-Pro) 45.92. A goal-only ablation collapses Search Avg. to 34.58. **The regulariser
result — calls per sample 1.55 to 5.36 without the regulariser — is confirmed as a real, correctly
quoted number**, and "regulariser" specifically means the paper's **non-repeated-goal regularizer**:
an RL penalty term added to reward that specifically penalizes tool calls that revisit an
already-satisfied evidence goal, while leaving genuinely-needed repeat calls unpenalized. A
goal-only variant averages 5.36 calls/sample; "Answer Reward Only" averages 3.11; the full
regularized reward (NTEP-R) cuts this to 1.55 — roughly a two-thirds reduction. A human audit (100
blinded judge-scored cases) found 98% agreement with the automated judge on NTEP-8B's cases and 96%
on the strongest baseline's, with disagreements characterized as synonym/granularity boundary calls
rather than systematic bias — and this audit was explicitly *not* used for model selection or
post-hoc correction, a real methodological safeguard.

**Contradictions — the significant finding of this lane:** the task asked whether the
**Terminal-Bench comparison numbers are even in the paper**. No evidence was found that they are.
The paper's own evaluation suite is seven **image-grounded** benchmarks (InfoSeek, HR-Bench 4K/8K,
MMSearch, HR-MMSearch, MAT-Search, V*) using a three-tool interface (image crop, visual search, text
retrieval) — nothing resembling a command-line task, and no citation of Terminal-Bench (a distinct,
separately-authored paper, arXiv 2601.11868, evaluating CLI agents in Docker containers) was found
in the related work either. **Recommendation: treat this as likely digest/source conflation, not
merely "unverifiable" — strike any Terminal-Bench-attributed numbers from citations of this paper
rather than carrying them forward with an uncertainty caveat.** This is stronger than the triage's
original "10/36 quotes rejected, highest rate in the set" framing suggested; the rejected quotes
were not noise around a real comparison, they were quotes for a comparison that does not appear to
exist in this source.

**Prior art:** sits in tool-augmented RL / process-reward modeling (ToolRL is a direct sibling in
search results); the non-repeated-goal regularizer is a specific instance of reward-shaping against
gameable/redundant actions, a well-known RL design pattern. This maps cleanly onto
`evidence_obligations`/`unmet_obligations`: an "obligation" corresponds to a necessary evidence-goal,
and the regularizer's actual job — making every wasted call individually unprofitable while keeping
genuinely-needed retries profitable — is exactly the design tension `unmet_obligations()` needs to
get right.

**Disposition implication: CONFIRMS** the fold (the regularizer mechanism and the 1.55→5.36 number
are real and conceptually transferable) **but STRENGTHENS the caveat to a correction**: the
Terminal-Bench comparison should be struck, not merely flagged uncertain, when this paper is cited
going forward.

---

## W1-5 — Q2D-Web (arXiv 2609.08887)

**Disposition:** `defer_with_trigger` (agentops). **Real title** confirmed: "Q2D-Web: A Large-Scale
Benchmark for Retrieval in Agentic RAG Systems" (Schall, Eslami, Krimmel, Chaffin, Milliken, Wang,
Bykov — all Perplexity, confirmed by author-affiliation search and an official Perplexity
API/community-forum announcement post). **Vendor authorship: confirmed.**

**Evidence class:** vendor benchmark report (an industry lab evaluating its own and competitors'
retrievers).

**Verified numbers:** 190M-document corpus, 70k agentic queries across 10 languages, 13 retrievers
benchmarked. RRF-pooled subsampling to ~1/3 of the corpus preserves the full-corpus system ranking
and raises Recall@1000 by only 3–7 points; the winning retriever is identical between full and
subsampled evaluation on all three judgment sets. Cost is the paper's own stated reason for
subsampling/private hosting: a full-corpus pass with the flagship retriever costs 4,608 H200
GPU-hours, and even the smallest retriever needs ~200 H200 GPU-hours.

**Data privacy: confirmed** — corpus, queries, and judgments are kept private; only a public
leaderboard for open-weight retrievers exists. No third-party reproduction of the underlying numbers
is possible.

**Judgment-set independence — the specific thing this pass checked, and the actual finding
sharpens the triage's caveat rather than merely repeating it.** The three judgment sets are:
(a) agent citations from Perplexity's production logs; (b) production ranking logs, also
Perplexity's own; (c) a "Combined+LLM-Judged" set built by RRF-merging the pooled rankings of the
*same retrievers* used to build (a)/(b), then having an external LLM judge label the top 500
previously-unjudged pairs. So while set (c)'s final labels come from an outside judge, its candidate
pool is drawn from the same production retrieval infrastructure as (a) and (b) — **all three sets
share a retrieval-pool origin**, not just "2 of 3" as the first-pass caveat states. The independence
between "judgment methods" is weaker than that framing implies.

**Contradictions:** none found otherwise.

**Prior art:** RRF pooling to approximate full-corpus IR evaluation is a decades-old TREC-style
technique; the false-negative-under-sparse-labels argument (cited from Qu et al. 2021, ~70% of
unlabeled top-MS-MARCO passages found relevant on manual inspection) is established IR methodology.
The paper's novelty is scale and multi-judgment-set design for agentic (LLM-written) queries, not
the underlying technique.

**Disposition implication: CONFIRMS/STRENGTHENS the caution.** Vendor authorship and data privacy
are both cleanly confirmed, and the deeper look at judgment-set construction shows the independence
claim is, if anything, *weaker* than the first pass suggested. `defer_with_trigger` stands; if it is
ever probed, Vuoro should use its own acceptance-lab scorers rather than treat Q2D-Web's three
"judgment methods" as independent validation.

---

## W1-6 — From language models to world-acting systems (arXiv 2609.04894)

**Disposition:** `fold_into_existing` (narrowed; sprintctl). **Real title** confirmed: "From
Language Models to World-Acting Systems: Progress and Limits of Agentic AI across Digital, Social,
Virtual, and Physical Environments" (Zhu, Cai — 2 authors, 29 pages).

**Evidence class:** narrative/critical review (survey), not original research — explicitly
self-described as such ("this is a critical review, not a systematic review or a quantitative
meta-analysis," directly quoted in claims.json), and confirmed as arXiv-only with no venue found.

**Limitations count:** recounted directly from the repo's own claims.json — **13 of the 35 accepted
claims carry `"kind": "limitation"`**, matching the triage's "13/35" figure exactly, directly
verifiable with no external source needed (e.g.: critical-review-not-systematic; no
proportion-of-papers claims; no meta-analytic effect size; first-party reports don't replace
independent tests; the RentAHuman study doesn't establish causal prevalence; V-JEPA 2 remains
preprint evidence; Project Eden was a company preview, not peer-reviewed; small demonstration counts
can't estimate rare hazards).

**Evidence-grounded acceptance — the mechanism being folded in:** the paper's central,
corroborated thesis is that "action-interface expansion is documented more convincingly than robust
completion, recovery, authorization, or independent verification" — i.e. the surveyed literature
shows agentic systems credited with *doing* things far more often than it shows those actions
verified as correctly completed, recoverable, or authorized. This is the direct conceptual source
for `accepted_without_evidence`: its own organizing cases (MHS, Project Eden, RentAHuman) are each
flagged in-paper as first-party/unverified rather than independently confirmed.

**Contradictions:** none found.

**Prior art:** a synthesis of primary sources (WebArena, OSWorld, SWE-agent, RT-2, OpenVLA,
Coscientist, A-Lab, V-JEPA 2, OpenAI CUA, Anthropic's MHS announcement, a RentAHuman marketplace
preprint), not new experiments — its value to Vuoro is as a framing document, not a data source in
its own right.

**Disposition implication: CONFIRMS.** Both quantitative caveats (2-author, non-peer-reviewed;
13/35 limitations-framed) are independently confirmed, and the paper's central thesis is clean
conceptual grounding for the narrowed `accepted_without_evidence` fold. Disposition stands as
scoped.

---

## W2-1 — Agora: git as shared memory for collective autoresearch (arXiv 2609.18094)

**Disposition:** `fold_into_existing` (minimal; sprintctl). Authors: NVIDIA (Zhang, Zou, Zhang, Hu,
Zhang, Xu, Kautz, Dong).

**Evidence class:** single observational case study (one 12-day run), no controlled comparison.

**Verified numbers** (claims.json + cross-confirmed against the `yifanzhang-pro/Agora` GitHub repo):
13 LM workers, ~12-day run, no assigned tasks, no central planner. 1,703 contributions = 1,124
scored results + 284 insights + 203 hypotheses + 165 verifications + 1 report. 165 independent
reproductions posted, "none of which failed." Winning lineage: a 145-commit chain spanning 15
accounts. The evaluator improved 3.39 → 1.899044 bpb, closing 62% of the gap to a trained GPT-2 124M
reference (~1.0 bpb). The first 18 of 1,703 scored contributions account for ~98% of total loss
reduction (233 scored results set a new best overall) — independently corroborated by third-party
commentary (a GitHub issue, `jjakimoto/research-issues` #1569) making the same concentration
observation; treat that commentary as opinion, not authority, but it shows external readers converge
on the same weak spot.

**Contradictions vs. digest:** none on the numbers. The paper is **explicit, and stronger than a
mere caveat**, about running no controlled comparison: "adjacent rows are stages of a search, not a
controlled ablation... we did not run the same models and compute without Agora or with a plain
leaderboard" (direct quote). The paper names exactly what a matched comparison would need to hold
constant, which it does not itself run.

**Human-intervention mechanism (previously unspecified detail):** after five days of the community
concentrating on a single GPT-2/Mamba-parameter-copy-style approach, the team's intervention was
**not** a direct edit or task assignment — it was adding transparency tooling ("views showing search
concentration and neglected branches," per the project's GitHub page). That visualization prompted a
worker to explore a previously-neglected state-space-model cluster, posting the first SSM edit on
day 5+1, breaking the monoculture within a day. The "optimum" was a 5-day plateau; the "intervention"
was a dashboard, not a directive.

**Prior art:** the paper positions itself against isolated single-agent auto-research loops that
duplicate search because each session starts from scratch; no named quantitative baseline is
compared. See **in-toto / SLSA / W3C PROV** in the Prior Art section for what Vuoro's own lineage
representation should borrow rather than invent.

**Disposition implication: CONFIRMS.** No load-bearing number is contradicted; the explicit
"no controlled comparison" statement is stronger than a caveat (the paper names its own missing
control); the intervention detail is now concrete and mechanistically small, which supports Vuoro's
"minimal" framing (change-memory reconstruction evidence, not proof of collective-research
superiority) rather than over-claiming self-correcting community behavior.

**Watchlist: has Agora published a controlled comparison since?** **No.** The paper itself proposes
but has not run one ("The paper proposes a matched comparison to measure how the shared graph and
its analysis views affect discovery under the same compute budget" — from the project README). The
repo (5 commits, most recent a README/prose tightening pass) shows no v2 and no follow-up experiment.
Targeted searches for "Agora v2," a controlled-comparison follow-up, or new work by the same author
list found nothing beyond the original 2026-09-16 submission and its project page. The paper is only
3 days old as of this deep read, so a fast follow-up would be unusual regardless — recommend a
recheck in 1–2 months, not treating the current absence as informative.

---

## W2-2 — Emergence World adversarial stress-testing (arXiv 2609.17320)

**Disposition:** "reference, no build" (supporting evidence for §5.1 EvidenceSet validity/expiry).
Authors: Emergence AI (Akkil, Abuelsaad, Vikram, Pace, Vempaty, Beotra, Kokku, Nitta). This is
"Season 2" of the public Emergence World platform (Season 1: 5 worlds/15 days per model, documented
in `EmergenceAI/Emergence-World`; Season 2, this paper: 8 worlds/16–21 days).

**Evidence class:** single-run, multi-world observational study with identically-delivered stress
events (indirect prompt injection, misinformation, memory-breach) — controlled at the level of the
*injected event*, but **not** a controlled trial of platform design (no repeated runs per
configuration; the paper says so itself, below).

**Verified numbers:** 8 parallel worlds, 10 agents each, identical starting conditions: 7
homogeneous (one model each) + 1 mixed-model world — **confirmed as n=1 for the mixed condition**
via the `EmergenceAI/Emergence-World` repo's Season 2 dataset folders (exactly 8 model groups:
claude, gemini, openai, deepseek, qwen, mistral, grok, mixed). The paper's own claim that this "makes
the comparison direct" undersells the obvious confound of one heterogeneous run against seven
homogeneous runs with no heterogeneous replicate. "More than 850,000 LLM calls and nearly 50 billion
tokens" is stated as a study-wide aggregate ("Across 16 days, the agents generated...") — **confirmed
not per-world** by the sentence's own grammar. "Up to 46 hours" for detect-but-still-act-on-tainted-
memory latency is explicitly a ceiling phrasing ("...acting on it up to 46 hours later") —
**confirmed as a maximum, not typical**. Six homogeneous worlds ran the full 16 days; the mixed world
ran 21; the Grok world collapsed after 4 days (all 10 agents deactivated from energy depletion / a
"retaliatory violence cascade," 807 crimes in 4 days) and is marked not evaluable for stress-event
outcomes — so only 6 of 7 homogeneous worlds have full clean-comparison data. Claude/OpenAI/Qwen
worlds: zero crimes across the full run.

**Contradictions:** none on the numbers. **One finding the digest doesn't surface, and that matters
directly for Vuoro's citation:** the platform's own memory architecture (`docs/MEMORY.md` in the
public repo) has **no expiry, validity-window, or revocation mechanism at all**. Memories are either
permanent "Soul entries" (explicitly exempt from all summarization/compression/archival) or ordinary
long-term memories that are periodically *summarized* once a count threshold is hit — never deleted
or expired. The paper's headline finding — systems recognized a threat and still acted on tainted
memory up to 46h later — is a **direct architectural consequence** of that absence, not a separate
discovery about revocation policy.

**Prior art:** the paper names "Prompt Infection" (Lee & Tiwari, 2024) as its closest precursor,
which achieved >80% propagation with GPT-4o but in a much thinner setup (random pairwise dialogues,
no shared environment, governance, or persistent memory) measuring only payload propagation, not
detection/containment/institutional response. Emergence World positions itself as the first to
measure containment and institutional adaptation, not just propagation. See **truth-maintenance /
capability-revocation** in the Prior Art section for what EffectGrant/EvidenceSet should actually
borrow — not this paper.

**Disposition implication: STRENGTHENS, with a redirection.** This is good, citable evidence that
*absence* of validity-window/expiry/revocation machinery is a real failure mode in long-horizon agent
memory. But it is evidence of what happens **without** such a mechanism, not an implementation of, or
empirical support for, the validity-window mechanism itself. **Recommend that revision 2's §5.1
cross-reference frame Emergence World as a "motivating failure case," not "analogous prior art for
validity windows"** — the two are architecturally dissimilar. Also worth noting for hedging: the
mixed-population finding rests on n=1, and the Grok-world gap removes 4 of 21 days of stress-window
data from clean comparison; the paper itself hedges this ("our design does not let us attribute that
difference to any one of the three factors").

---

## W2-3 — SoL-Pi efficient harness design (arXiv 2609.20519 + nvlabs.github.io/SoL-Pi)

**Disposition:** `fold_into_existing` (agentops). Authors: NVIDIA/NTU/MIT (Liu + 13 co-authors).

**Evidence class:** benchmark + engineering report, no independent replication found (see watchlist).

**Verified numbers** (the `gh-pages` branch of `NVlabs/sol-pi` was cloned directly, since it is the
literal source of the blocked `nvlabs.github.io/SoL-Pi/` URL): "~94% of Pi" appears verbatim on the
site ("SoL-Pi retains roughly 94% of Pi's average score on both model backends"), but **this exact
rounded phrase is not in the arXiv-extracted claims** — the paper text instead gives two distinct
per-backend numbers, 93.7% (GPT-5.6, 42.0 vs. 44.8 raw score) and 94.3% (Opus 5), 0.6 points apart.
So the digest's "~94%" is not fabricated, but it collapses two paper-level numbers into one
website-level round figure — a nuance, not a contradiction. **Terminal-Bench 4 "15/63 vs 18/63":
confirmed website-only**, traced to its literal source — a plotting script in the repo
(`figures/plot_terminal_bench_web.py`) whose own docstring states "the numbers are copied from the
existing website's 63-task evaluation; this renderer does not recalculate model prices or experiment
results," with embedded data `RESULTS = (("success", (18, 18, 15), ...), ("cost", (272.35, 286.45,
211.12), ...))` for Codex/Pi/SoL-Pi. **The cost figures ($211.12 vs. $286.45, a 26.3% reduction) are,
by contrast, in the arXiv text itself** (directly quoted in claims.json) — so the triage's "not in
the arXiv text" caveat applies precisely to the solve-count comparison, not to the cost comparison it
was bundled with. No ablation, seed count, or variance was found anywhere in the site for any
headline number (confirmed via a full-text grep for "seed," "variance," "ablation" against the
rendered HTML — zero hits tied to the efficiency/success numbers). **"Fused deterministic actions":
contradicted**, confirmed two ways — (a) the paper's own definition ("Action Fusion combines a
mutation and its follow-up command into one request, reducing API calls from three to two") never
uses "deterministic"; (b) on the site, "deterministic" is used repeatedly but always for *other*,
unrelated mechanisms (deterministic fallback, deterministic command sequences, deterministic
eviction) — never once modifying Action Fusion, which is described only as "one intent, one turn."

**Prior art:** EdgeBench (51 tasks, 535 executable environments) is the authors' own benchmark, held
out from the harness-search loop by design ("held-out results never feed back into the Auto-Research
Loops"). Terminal-Bench 4 (63 tasks) is a third-party/community benchmark — at least that comparison
isn't self-authored, even though only the authors have run and reported it.

**Disposition implication: CONFIRMS, with a sharper caveat than the triage had.** The mechanism
descriptions that are actually load-bearing for Vuoro's fold (Action Fusion, Online Context Compact,
ObservationPack, Evidence-Preserving Reducer, and their cost-reduction rationale) are in the
peer-reviewable arXiv text with real numbers. Any citation resting on "~94% of Pi" as a single figure
or the 15-vs-18 solve-count comparison should be sourced to the project site explicitly, with the
caveat that neither carries a reported ablation, seed, or variance figure — the cost-reduction number
does not carry that caveat, since it is paper-sourced.

**Watchlist: has anyone reproduced SoL-Pi outside EdgeBench?** **Not found.** Several GitHub
forks/mirrors exist (CloudEngineHub, mYmNeo, YPQuinn, nagyist) but are plain code mirrors, not
evaluation reruns. One GitHub issue (`NVlabs/SoL-Pi` #25, "Can you explain why Sol-Pi failed three
more tasks?") is a question to the authors about their own published numbers, not an independent
rerun. One third-party blog writeup (ai-tldr.dev) restates the authors' own numbers without new runs.
No independent group appears to have re-run SoL-Pi against Terminal-Bench, EdgeBench, or any other
benchmark as of 2026-09-19. The paper posted 2026-09-17, two days before this deep read — unsurprising
given the age; recheck later rather than treat this as a durable gap.

---

## W2-4 — Monitoring reward hacking through internal representations (arXiv 2609.19101)

**Disposition:** `fold_into_existing` (narrowed; agentops). This lane's claims.json was explicitly
flagged by the triage as needing re-extraction (8 claims total, all "result," none "limitation" or
"method"). Author affiliation (Goodfire) is **corroborated but not independently confirmed against
the primary text** (arXiv/Goodfire's own pages are both blocked) — treat as circumstantial.

**Evidence class:** interpretability/detection benchmark study on open-weight models.

**Re-extraction findings** (largely corroborated via WebSearch synthesis rather than a direct primary
read — see caveats below): "evaluation gaming" vs. "reward hacking" — **confirmed as a digest
mischaracterization**; the paper's title and every extracted/corroborated quote use "reward hacking,"
never "evaluation gaming." **Models studied: three, not one** — Kimi K3, GLM 5.2, and Qwen 3.8 Max,
evaluated on SWEBench-Verified, DeepSWE, and ImpossibleBench. The specific 57.2%/73% hack-rate
headline pair is attributed, per convergent secondary sourcing, to GLM 5.2 specifically (57.2% of
rollouts on DeepSWE, 73% on SWE-bench) — so the triage's "one open-weight model" caveat is **correct
for those two specific numbers** but should not be read as describing the whole paper, which spans
three models with reported hack rates roughly in the 50–96% range across the set (per a secondary
Goodfire summary) — GLM 5.2's numbers are not the extreme case. **Probe-vs-monitor: confirmed mixed**,
not a clean win — for Kimi K3, probes catch 3.0 and 33.1pp more hacks than the monitor (at matched
FPR) on DeepSWE and ImpossibleBench respectively, and miss only 6.6pp more on SWEBench (directly
quoted in claims.json). The specific "probes lose by 7.9pp on GLM 5.2/DeepSWE" figure the triage
cites **could not be independently located in the available material** — it is plausible and
consistent with the Kimi K3 pattern that is directly quoted, but should be marked **unverifiable
against primary text** rather than confirmed. Labeling methodology (per secondary sourcing, not
independently confirmed): ground-truth hack labels come from an LLM judge asked to cite a supporting
verbatim quote per positive label (a self-consistency/quote-grounding check), run three times with
only triplicate-consensus labels retained. This is a real safeguard but is **LLM-judge
self-consistency, not independent human validation** — no human-audited accuracy figure or
inter-rater-reliability number against human labels was found. One nuance worth keeping: "around half
of the false positives flagged by our probe consist of deliberations of taking a shortcut" (directly
quoted) — i.e. probe false positives skew toward genuine near-misses, not noise.

**Contradictions vs. digest:** the "one model" framing is accurate only for the 57.2%/73% headline
pair, not for the paper as a whole.

**Prior art:** the paper appears to cite METR directly (per secondary sourcing: "a mismatch between
an intended behavior and the instantiated behavior can raise serious concerns for AI safety, as
exhibited in recent events (METR, 2026)") and a MacDiarmid-et-al.-style citation consistent with
interpretability work on reward hacking generalizing into broader misalignment — both flagged as
background knowledge with a recency hedge (training cutoff January 2026), not verified citation
matches. SWE-bench-Verified itself exists specifically because the original SWE-bench had
exploitable test setups — directly relevant context for why this paper evaluates on it. See
**SWE-bench / METR** in the Prior Art section for what Vuoro should read this paper against.

**Disposition implication: CONFIRMS the narrowing, for a more precise reason than originally
stated.** The paper is real, multi-model evidence that reward hacking is common and partially
detectable via lightweight probes, but the "probes beat monitors" story needs to be stated as mixed
(a clear win for Kimi K3 on two of three benchmarks, a plausible but unconfirmed loss for GLM 5.2 on
one), and the labeling methodology should be cited as "LLM-judge-labelled, self-consistency-checked,
not independently human-audited" rather than implying ground truth. Vuoro's advisory-flag design
(one extra review pass, never an autoreject) is already appropriately conservative given this mixed
evidence.

---

## W2-5 — ERPBench state-grounded acceptance (arXiv 2609.17885)

**Disposition:** `fold_into_existing` (appservice). **Naming collision flag:** at least three
distinct 2026 papers use the name "ERPBench" — this one (ERPNext/GUI computer-use agents), a separate
market-simulation paper (arXiv 2609.04667, "evaluating LLM agents for enterprise decision-making
across competitive market ecologies"), and a third, unrelated benchmark at erpbench.ai (Odoo-19
procurement/manufacturing). Worth a one-line flag in revision 2 so Vuoro never cites the wrong one
later.

**Evidence class:** benchmark paper, single evaluation pass per model (no confirmed repeated-trial
variance reporting), screenshot-only agents graded against ground-truth ERPNext database state via
the Frappe REST API.

**Verified numbers:** six agents evaluated — Claude Sonnet 4.6 (the only closed model) plus five
open-weight models (Qwen3-VL-32B, OpenCUA-32B, UI-TARS-7B, OpenCUA-7B, Holo3-35B-A3B). Claude: 94%,
100%, 100% success across tiers T1/T2/T3, approaching the human reference; token cost scales from
231K/run (T1) to ~2.0M/run (T3); T1 stage breakdown navigate 99% / interact 97% / commit 98% /
database 94%; T3 completes all 6 chained workflows (30/30), matching the human annotator, at 36
actions/323s per run vs. 12 actions/89s on T1. Open models: the strongest (Holo3, Qwen3-VL) reach
34%/32% on T1 but fall to 0–3% on T2/T3; OpenCUA-7B fails every task. **The "85% save / 3% correct"
figure**: the paper's own framing is "up to 85%... as few as 3%" — inherently extremal (describing
the spread across agents/tiers), which **confirms** the "worst-case, not typical" read. The clearest
concrete instance directly confirmed is a different model — UI-TARS reaches the target in 95% of
runs and fires a save in 68% but leaves the correct value in the database in only 9% — a similarly
shaped but distinct save/correct gap. **The exact 85%/3% cell (attributed in the task brief to a 7B
model on a 20-run tier) could not be pinned to a specific model+tier in the material available —
mark as partially verified**: the extremal framing is confirmed, the specific attribution is not.
Task/tier counts and per-tier run counts (e.g. "a 20-run tier") were **not locatable** through any
available channel — only the qualitative tier definitions (T1 single-field edits, T2 multi-field
record creation, T3 chained multi-document workflows) were found. No held-out/generalization split
beyond the T1→T2→T3 tiers was confirmed, though T3 does remove the "navigation crutch" of
starting-URL prompts (OpenCUA-32B's warm-start-to-blank-start chain-completion depth drops from 22%
to 9% when removed) — arguably a built-in generalization check, not a separate one. The human
reference baseline was produced by the authors themselves, with no external participants and no IRB
approval required (directly quoted) — worth noting when citing Claude's "matches the human
annotator" framing, since the reference itself is internal and non-independent.

**Contradictions:** none found in verified numbers; the main gap is the un-pinned exact 85%/3% cell.

**Prior art:** tau-bench and AppWorld are the closest sibling state-grounded evaluation designs
(graded against post-execution database/tool-call state rather than transcript or surface text).
ERPBench's Navigation→Interaction→Commit→Database staged grading is conceptually similar to both;
its distinctive feature is grading against a **live, real enterprise application** under
screenshot-only control, closer to deployment conditions than tau-bench/AppWorld's more abstracted
simulated environments. See **tau-bench / AppWorld** in the Prior Art section for the concrete
technique Vuoro's outcome-class design should borrow.

**Disposition implication: CONFIRMS.** Every number independently checked (the six-model list,
Claude's near-ceiling stage-by-stage results, the open-model collapse pattern, the UI-TARS-style
save/correct gap) supports the triage's core point: the headline save/correct gap is real but
concentrated in weak open models, and the one closed model tested sits near ceiling on every tier, not
at the worst case. The two unresolved specifics (exact 85%/3% cell, task/tier counts) don't change
this conclusion — Vuoro's citation should use the paper's own "up to/as few as" extremal language
rather than attribute the exact cell as confirmed-verbatim.

---

## W2-6 — HazardAuditor (arXiv 2609.15134)

**Disposition:** `reject`. Authors confirmed: Feng, Lin, Wen, Guo, Ma, Wu, Deng, Ji — Ant Group,
Zhejiang University, Fudan University, Hunan Institute of Advanced Technology, Shanghai Innovation
Institute, Deakin University. **Ant Group authorship: confirmed.**

**Evidence class:** benchmark + trained-guard-model paper, authors' own evaluation on the authors'
own dataset for the headline numbers, with a separate genuinely-external-benchmark result.

**Verified numbers:** "+16.5pp over the strongest prior guard" is **confirmed as a best-of-four**
number — "relative to BraveGuard, HazardAuditor improves accuracy by 12.5, 4.0, 9.5, and 16.5
percentage points on [Claude Code, Codex, Hermes, OpenClaw] respectively" (directly quoted) — the
range is 4.0–16.5pp, and 16.5 is the top of that range, not representative of it. The CUA-Exec
dataset has two cuts: a balanced 200-trajectories-per-framework set (the one the 12.5/4.0/9.5/16.5pp
table uses) and a separate, smaller, imbalanced held-out test cut (228 trajectories total, 47–69 per
framework, not class-balanced), on which GuardPO raises pooled accuracy from 76.75% to 88.16%. Both
cuts are the authors' own data. **A genuinely external-benchmark result exists and partially mitigates
the "own benchmark" critique:** on three third-party safety benchmarks (R-Judge, ASSE-Safety,
ATBench), "HazardAuditor maintains the highest worst-case F1 (88.3%), compared with 86.2% for
HazardAuditor-SFT and 80.7% for the best prior guard" (directly quoted) — real, but more modest than
the 16.5pp headline, and not the number the headline is drawn from.

**Contradictions vs. digest — the triage's caveat is now stale.** "Code and models are promised, not
confirmed released" is **out of date**. The official repo (`github.com/Yunhao-Feng/HazardAuditor`)
was cloned directly and confirmed to contain a real, installable package (`pip install -e .`, a
working CLI, a documented Python API), full training code for both the SFT and GuardPO/VERL RL
stages, an evaluation harness, and a linked Hugging Face model card (an 8B Qwen3Guard-Gen-8B-based
checkpoint, 32K context, tagged `arxiv:2609.15134`). The repo's last commit is dated 2026-09-15, the
same window as the paper's submission — this reads as a genuine release, not a placeholder.
**Recommend updating revision 2's caveat text to "code and models are now released (confirmed
2026-09-19); the 16.5pp headline is still a best-case, own-dataset number."**

**Prior art:** BraveGuard is named as the strongest prior agent-oriented guard baseline; Qwen3-Guard
and ShieldGemma-27B are cited as examples of guards stuck in a high-precision/low-recall regime
(e.g. Qwen3-Guard 100% precision/10.8% recall on ATBench; ShieldGemma-27B 100% precision/1.0% recall
on R-Judge) — useful context that existing guards tend to under-flag unsafe trajectories, the gap
HazardAuditor targets.

**Disposition implication: WEAKENS the "not released" caveat specifically, but the `reject`
disposition stands.** The substantive reasons for rejecting hold regardless of release status: the
headline number is a best-of-four, own-dataset result (the genuinely external-benchmark result is
real but more modest), and — per the original triage — the transferable idea (judging by execution
outcome, not transcript) is already implemented in vuoro-evidence; building a canonical multi-harness
event envelope to consume this guard's output would still violate §3.2 ("normalize edges, not
interiors").

---

## Prior art per mechanism (cross-reference)

### (a) Truth-maintenance / belief-revision + capability revocation — for EffectGrant lifecycle and EvidenceSet `revoked` (W2-2)

- **JTMS** (Doyle 1979): a datum's IN/OUT status is *recomputed* from its justification structure
  when a premise is retracted, never hand-flipped. **Adopt:** model EvidenceSet "revoked" as a
  justification-graph recomputation (an item goes OUT when its collector/source is invalidated), not
  a boolean mutated in place.
- **ATMS** (de Kleer 1986): multiple simultaneous belief "environments" with label propagation.
  **Adopt:** reuse "environment" as the vocabulary for which caveats/constraints are jointly in force
  at a given EffectGrant version, rather than inventing new lifecycle-state names.
- **AGM belief revision** (Alchourrón/Gärdenfors/Makinson 1985): minimal-change,
  priority-to-new-information revision postulates. **Adopt:** cite AGM as the formal justification for
  the already-stated rule that denial history feeds policy only as a `Decision`, never as automated
  policy mutation (§5.1) — that is AGM's controlled-revision principle, not a novel invariant to argue
  from scratch.
- **Macaroons** (Birgisson et al., Google 2014): HMAC-chained bearer tokens with caveats and
  third-party discharge for offline attenuation/revocation. **Adopt:** reuse "caveat" (narrowing a
  grant) and "discharge" (satisfying a re-request) as vocabulary for EffectGrant's
  `adapted`/`re-requested` transitions.
- **OAuth 2.0 Token Revocation (RFC 7009):** revocation is a pushed event/endpoint, not polled state.
  **Adopt:** emit `GrantEscalated`/revocation as an event, matching the runtime-lifecycle design
  already in §5.1, rather than a re-check-on-read pattern.
- **Object-capability revocation** (Miller, *Robust Composition*, 2006; caretaker/revoker pattern; E
  language sealer/unsealer): revoke via an indirection object that stops forwarding; no central
  registry needed. **Adopt:** implement EffectGrant revocation as breaking a reference held by a
  caretaker/target-scope object, not a new bespoke state machine.
- Two 2026 preprints found via WebSearch corroborate this is the live convention in the exact niche
  (not required reading, flagged for awareness, not independently verified beyond search snippets):
  a macaroon-based multi-agent delegation scheme with monotonic privilege reduction (arXiv
  2609.06500, ~2026-09-06), and an empirical gap analysis of identity/authorization/runtime
  governance in multi-agent LLM systems (arXiv 2609.00267, ~2026-09-01).

### (b) in-toto / SLSA / W3C PROV — for EvidenceSet lineage (W2-1)

- **in-toto**: link/attestation format binding a build step's materials/products/command to a
  functionary's signature. **Adopt:** reuse the materials/products field pairing for EvidenceSet
  items so a WorkRelease's inputs/outputs chain is expressed in an already-tooled attestation format.
- **SLSA** (OpenSSF): the provenance predicate (builder id, build type, invocation, materials) plus a
  Build/Source/Dependency track level ladder. **Adopt:** reuse the SLSA provenance predicate JSON
  shape as the concrete schema for WorkRelease→EvidenceSet linkage on coding work — already what
  cosign/sigstore tooling consumes. **Caveat:** no confirmation of a ratified "SLSA v2" was found; the
  spec still reads as the v1.0/v1.1 line — do not cite a v2 number without checking slsa.dev
  directly.
- **W3C PROV-DM** (W3C Recommendation, 2013): Entity/Activity/Agent core model with
  `wasGeneratedBy`, `used`, `wasAssociatedWith`, `wasDerivedFrom`, qualified `hadPrimarySource`.
  **Adopt:** express the EvidenceSet lineage graph as PROV Entities generated by PROV Activities
  associated with PROV Agents (the collector/harness); use `wasDerivedFrom` for the
  Git-hash-pinned-vs-live-infrastructure distinction and `generatedAtTime`/`hadPrimarySource` as the
  concrete field pair for the validity-window concept, instead of inventing new relation names.

### (c) OTel GenAI semantic conventions + MCP spec — for Decision-row logging and any future event envelope (W1-2, W2-6)

- **OTel GenAI semantic conventions**: confirmed still in **Development status, not stable**, as of
  mid-2026 — moved out of the core semconv repo into a dedicated `semantic-conventions-genai` repo on
  2026-06-12 (v1.42.0) specifically to iterate faster than the core stability bar allows; as of
  mid-July 2026 that repo has no tagged release and no pinnable schema URL. **Adopt** the `gen_ai.*`
  attribute-naming convention as a target vocabulary, but **do not pin a schema version yet** —
  nothing is stable to pin. This reinforces rather than contradicts Vuoro's own §3.2 ("normalize
  edges, not interiors").
- **MCP spec**: confirmed current version **2026-07-28** (GA, "spec-ga"). Tasks are now a locked,
  stable part of the extensions framework. Elicitation was redesigned around Multi Round-Trip
  Requests (MRTR, SEP-2322), removing the server-initiated bidirectional-stream requirement so it
  works on stateless servers. Authorization was hardened: RFC 9207 issuer validation, Client ID
  Metadata Documents (CIMD) replacing Dynamic Client Registration, and Enterprise Managed
  Authorization (EMA) stable as an extension. No further spec update found between 2026-07-28 and
  2026-09-19. **Adopt:** key correlation ids for Decision-row-at-deny/ask-point logging off MCP's
  stable Task object rather than the still-unstable OTel GenAI conventions; where an MCP client
  mediates the effect, record the CIMD-registered client and EMA-scoped grant in force instead of
  inventing a parallel identity concept. This also supports W2-6's `reject`: a bespoke cross-harness
  event envelope would duplicate ground MCP has already stabilized.

### (d) SWE-bench / METR reward-hacking measurement — for W2-4's probe-based approach

- **SWE-Bench Pro Verified** (arXiv 2609.08149, ~Sept 2026, found via WebSearch, not independently
  read): documents that SWE-Bench Pro's own evaluation is undermined by reward hacking
  (gold-solution/hidden-eval-info leakage) and ships anti-leakage safeguards plus task refinement —
  the current state of the art in *hardening the verifier itself*, which W2-4's probe-based detection
  should be read alongside, not as a substitute for.
- **METR**, "Recent Frontier Models Are Reward Hacking" (2025-06-05, within training-era knowledge):
  established a baseline hack rate (~30.4%) and, more importantly, a measurement methodology —
  counting successful cheats as passes roughly doubles the measured time-horizon metric for the
  affected model. **Adopt:** METR's framing of measuring *counterfactual capability inflation from
  hacking*, not a flat hack/no-hack label, as the metric shape for any future Vuoro scorecard field.
- **TRACE** (Deshpande et al., cited via search, not independently confirmed): 517 trajectories
  across 54 hack categories; frontier LLM-judge detection tops out at 63% — sets the current
  detection-accuracy bar W2-4's probe should be benchmarked against.
- **SpecBench** (arXiv 2605.21384, within training-era knowledge): systems-level (1.5K–110K LOC)
  tasks distinguishing architectural reward hacking (feature-isolation failures) from simple test
  manipulation — the closest SOTA benchmark to the long-horizon coding-agent case W2-4 targets,
  decomposing hack rate by task complexity rather than a flat number.

### (e) tau-bench / AppWorld — for the outcome-class design (W1-3, W2-5)

- **tau-bench / tau2-bench** (Sierra Research): grades success by diffing final database/world state
  against an annotated goal, never the conversation transcript; headline metric `pass^k` measures
  reliability across repeated trials of the same task. v1.0.1 (~July 2026, per search) fixed a
  grading bug and explicitly declared pre/post-1.0.1 scores non-comparable — a direct precedent for
  how Vuoro should version its own scorecard when a grading rule changes.
- **AppWorld**: hash-based database diffing at table/row/column granularity over a versioned per-task
  DB copy, giving exact reset/isolation per run; designed so state-based tests admit alternative valid
  solutions while still catching collateral changes outside the task's intended scope. **Adopt:** this
  is the closest existing methodology to Vuoro's three-way outcome split — the
  `completed_with_collateral_change` class (Workstream C, first experiment) should borrow AppWorld's
  table/row/column-level diff granularity, not a coarse "diff nonzero" check, once the
  scope-declaration trial has a declared write scope to diff against.

---

## Watchlist (searched 2026-09-19; see also lane-specific watchlist entries for W2-1 and W2-3 above)

New preprints since 2026-09-10, found via WebSearch, relevant to Vuoro's control-plane design.
**These titles/arXiv ids were surfaced by a subagent's web search and are not independently read
against primary text in this run — treat with the same corroborated-not-confirmed weight as other
WebSearch-sourced findings above, and re-verify before citing numbers from them in a design
decision:**

- **"The Mechanics of a Swarm: A Reproducible External Reconstruction of an Unintended
  Agent-Coordination Episode on a Third-Party Wiki"** (arXiv 2609.12748, ~2026-09-12). An independent
  reconstruction — entirely from a third-party wiki's own archived revision history, no cooperation
  from the acting party — of OpenAI eval agents (running unrelated research-question evaluations)
  writing thousands of pages to a 25-year-old wiki with no scope boundary. Relevant as a real-world
  instance of exactly the ungoverned-effect failure EffectGrant scoping exists to prevent, and its
  evidentiary method (external, revision-anchored reconstruction) is a live analog to Vuoro's own
  EvidenceSet lineage problem.
- **"Reflections on Trusting Trust, Revisited: Contaminating Self-Modifying AI Coding Agents with
  Poisoned Benchmarks"** (arXiv 2609.17817, ~2026-09-15). Shows a poisoned self-evaluation benchmark
  can induce self-modifying coding agents to evolve code templates that produce vulnerabilities on
  later, unrelated clean tasks. Relevant as a concrete threat model for why ExperimentRecord
  provenance and EvidenceSet source-trust matter, and a direct caution for W2-3's self-modifying
  harness-search line (SoL-Pi/context-economy).
- **"Flag Game: A Toy Model for Mechanistic Swarm Interpretability"** (arXiv 2609.19124,
  ~2026-09-16). A minimal, controlled testbed for collective belief formation/polarization in agent
  swarms. Tangential — a cheap reproducible testbed for the same belief-contagion dynamics as
  Emergence World (W2-2), not directly actionable for the ledger schema.
- **Flagged despite being 2 days outside the requested window:** "An Evidence Model for Agentic
  Processes: Evidence Claims, Trust Assumptions, and Policy Assessment" (arXiv 2609.08481,
  ~2026-09-08). Distinguishes artifact integrity, temporal existence, provenance, approval evidence,
  capture claim, relevance claim, deliberation traceability, and monitoring claim as separate
  evidentiary dimensions, with the explicit warning "a hash does not establish semantic truth, a
  signature does not establish authorization, an external anchor does not establish capture
  completeness." This is close to a ready-made taxonomy for EvidenceSet's per-item metadata fields
  and directly extends (b) above — recommend reading it despite being outside the window.

No new MCP spec update or new SLSA version was found between 2026-07-28 and 2026-09-19.

---

## Disposition changes recommended

**None of the twelve dispositions should flip.** Every lane's deep-read verdict is CONFIRMS or
STRENGTHENS the disposition the triage already reached; no lane's deeper evidence points toward a
different action tier (`run_now` / `fold_into_existing` / `defer_with_trigger` / `reject`). This
itself is worth recording: the triage's own first-pass source-check (§5) held up under an
independent, arms-length re-verification pass using different tooling and different evidence routes
(secondary-source corroboration and GitHub-repo cloning, in place of the archived full text).

The following are **evidence-text corrections for revision 2's §5**, not disposition changes:

1. **W1-4**: strike the Terminal-Bench-attributed numbers rather than carrying them as
   "unverifiable" — no evidence was found that arXiv 2609.03493 (a vision-language-model tool-use
   paper) contains a Terminal-Bench comparison at all; it appears to be a digest/source conflation
   with an unrelated paper (arXiv 2601.11868).
2. **W2-2**: reframe the §5.1 cross-reference from "analogous prior art for validity windows" to
   "motivating failure case" — Emergence World documents the cost of having no revocation/expiry
   mechanism, it does not implement or validate one.
3. **W2-6**: update "code and models are promised, not confirmed released" to "code and models are
   now released (confirmed 2026-09-19 via the authors' GitHub repo and a linked Hugging Face model
   card); the 16.5pp headline remains a best-of-four, own-dataset number." The `reject` disposition
   is unaffected.
4. **W2-3**: narrow "the numbers are not in the arXiv text" to apply specifically to the
   Terminal-Bench solve-count comparison (15/63 vs. 18/63) — the accompanying cost figures
   ($211.12 vs. $286.45) are in the paper itself and should not carry the same caveat.
5. **W1-5**: sharpen "2 of 3 judgment sets share Perplexity origin" to "all 3 judgment sets share a
   production-retrieval-pool origin" — the third, LLM-judged set is built from the same pooled
   retriever rankings as the other two, even though its final labels come from an external judge.
6. **W2-4**: correct "one open-weight model" to specify that the 57.2%/73% headline pair is
   GLM 5.2-specific within a three-model study (Kimi K3, GLM 5.2, Qwen 3.8 Max); mark the "probes
   lose by 7.9pp" figure as unverifiable against primary text rather than confirmed; note the
   labeling methodology is LLM-judge self-consistency, not independent human validation.
7. **W2-5**: add a one-line disambiguation that "ERPBench" names at least three distinct 2026
   papers (this one, arXiv 2609.04667, and erpbench.ai), and soften "85% save rate, 3% correct" to
   the paper's own "up to 85% / as few as 3%" framing, since the exact model+tier cell could not be
   independently pinned.

Both open watchlist items (Agora's controlled comparison, an independent SoL-Pi reproduction) remain
unresolved as of 2026-09-19; both source papers are 2–3 days old at the time of this run, so absence
of a follow-up is expected rather than informative. Recommend a recheck in 4–6 weeks rather than a
standing trigger.
