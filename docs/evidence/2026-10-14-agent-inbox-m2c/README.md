# Agent inbox M2c: one recorded cross-runtime continuation

Live run, 2026-10-14 14:22–14:35 UTC, against vuoro-shared. That deployment is
vuoro-service 0.1.95, image `sha256:b3e4097f…`, work schema 25, 102 catalog
operations, revision `fd49d79d…`, and identity revision 12. The run follows
roadmap §7 (`docs/plans/2026-10-14-agent-coordination-roadmap.md`) and the
contract in `docs/architecture/agent-directory-and-inbox.md`.

| Role | Principal | Runtime |
|---|---|---|
| Sender | `vuoro-static:workstation-inbox-native:0` | Claude Code on the workstation; sprintctl 0.19.0 |
| Recipient | `vuoro-static:devbox-inbox-native:0`, instance `ins_01M4XCNCR3NVZMAZ4SE2GD8H6Y` | agentops host adapter (agentops PR #343: `42932e5` for the histories, `3a1b4a3` after review round 2) on devbox, launching OpenCode 1.18.33 (`opencode serve` on loopback) with model `opencode-go/deepseek-v4-flash` |

Both identities hold only `work:read` and `work:evidence`, for repository
`agentops` and workspace `vuoro-native:agentops-coord:0`. The work item is
agentops #2654.

The adapter ran as a systemd user unit with explicit
`SPRINTCTL_BACKEND=served` and `SPRINTCTL_VUORO_PROFILE`, without direnv.
The `opencode serve` unit sets the same two variables, so the successor's
`sprintctl` calls run as `devbox-inbox-native`.

Files:

- `records.json` holds `work.message.status-v1` for all 9 messages: the
  envelope plus 49 receipts. This is the owner's durable record.
- `adapter_status.json` holds the adapter's local processing records: 9 records
  for one instance.
- `successor-notes.txt` holds the session notes on each successor run, read
  back through the owner (`work.run.predecessor-context-v1` from a reader run).

## Success path (message `msg_01M4XCR3F4XF5354B7CJTSCDCQ`)

1. The directory read (`work.agent.list-v1`) shows the adapter instance
   registered with `observed` and `reachable(opencode-serve)` current, both
   self-reported.
2. The sender registered its own run `run_MF762HM5CEEZ7SV8DBPNY6YXSB` and wrote
   note 1 on it. It then minted a `vuoro-work-link/v1` for item 2654 with that
   run as predecessor, and sent `continue` to the devbox principal (inbox
   seq 1).
3. The adapter recorded `observed`, `acknowledged` and `accepted`, after
   checking the message independently against its policy file. It then
   registered the successor run `run_W5NFWA2N3SG9JSK416FA7B1Q4B` under owner
   key `inbox.<message_id>` and recorded `run-linked`.
4. The adapter created the OpenCode session `ses_ec533e3c…` titled
   `vuoro-msg:<message_id>`. The successor ran `sprintctl run context`, which
   showed the predecessor's note 1, then `sprintctl run note` with key
   `inbox.<message_id>.note`, which wrote note 2.
5. The adapter recorded `result-linked` with outcome `completed`. Its evidence
   refs are the run and the harness session; from F1 onward they also include
   the session note (see the fixes below).
6. The sender rebuilt the chain using only owner reads: the message status
   (5 receipts) and the successor's notes.

## Induced failure histories

Processed by: **unit** is the installed systemd user unit (`agentops-host-adapter.service`); **in-process** is
the fault-injection driver running the adapter's own `Adapter` class with a checkpoint hook (service
stopped, same state directory); **unit after crash** means the in-process run was killed and the unit
then completed the record.

| # | Failure (roadmap §7) | How it was induced | Processed by | Observed | Result |
|---|---|---|---|---|---|
| F1 | Sender retries after an uncertain response | Same envelope sent twice; then the same `message_id` with an altered body | unit | Second send `replayed: true`, same `inbox_seq` 2 and envelope digest; the altered one was refused with `message-id-conflict` | One logical message |
| F2 | Recipient receives the same message repeatedly | Adapter stopped, read cursor rewound 2 → 0, re-polled | in-process | Both messages re-read; still 2 processing records; receipts unchanged (5 each) | One processing record |
| F3 | Adapter crashes before launch | Hard exit (`os._exit`) at `run_registered_persisted`, then the service restarted | unit after crash | Resumed with the same run `run_EF84…`; one `run-linked`; completed | Resumed safely |
| F4 | Adapter crashes after launch, before acknowledgment | Hard exit at `session_created` (session exists, id not persisted), then restart | unit after crash | Session found by title and adopted; exactly 1 session with that title; completed; not prompted twice | Existing run found |
| F4b | Same, with two matching sessions | As F4, plus a second session created with the same title | unit after crash; operator `resolve` | `launch-ambiguous` receipt carrying the run id; neither session prompted (0 messages each); no relaunch. `resolve --abandon` was refused while the service held the lock (exit 3); after a stop it recorded `result-linked` `abandoned` / `operator-abandoned` | Ambiguity surfaced; explicit operator close |
| F5 | Effect succeeds before result recording fails | Successor's note write replayed with key `inbox.<msg>.note` | owner replay via `sprintctl run note` on devbox | Same key and text return note 2 with the original `created_at`; same key with different text gives `idempotency-conflict` | Owner deduplicates |
| F6 | Authorization changes before execution | Policy swapped to remove the sender after `accepted`, before launch | in-process | Policy re-read before launch: `result-linked` `not-started`, reason `sender-not-allowed`; 0 sessions; 0 notes | Refused under current policy |
| F7 | Cancellation races with execution | `cancel` 0.8 s after `run-linked`, while launched | unit | The session finished before the adapter's next tick: outcome `completed`, and `cancel-requested` stays in the record; a repeated cancel returns the first (`replayed: true`) | Records what happened |
| F7b | Cancel after completion | `cancel` 5 s after `result-linked` | unit | Cancel accepted and recorded after the result; outcome remains `completed` | Cancel never implies rollback |
| F7c | Cancel while the session is busy | Hard exit at `prompt_sent`, then cancel, then restart | unit after crash | The adapter aborted the busy session: `result-linked` `canceled` (`MessageAbortedError`); 0 notes | Aborted, recorded as canceled |

Ten induced histories: 3 processed entirely by the unit (F1, F7, F7b), 4 by the unit after an
in-process crash (F3, F4, F4b, F7c), 2 entirely in-process (F2, F6) and 1 owner-side replay (F5).
In-process runs used the live owner and the live `opencode serve`; nothing was mocked.

**Owner-side effects.** Each of the 6 completed runs has exactly one session
note. The 3 runs that did not complete (abandoned, not started, canceled)
have none. See `successor-notes.txt`.

## Defects found live and fixed on agentops PR #343

1. **Envelope idempotency key.** `VuoroOwner` sent an envelope idempotency key
   to operations whose catalog entry is `idempotency: not-allowed`. The live
   service refuses this with `idempotency-key-not-allowed`. Registration,
   observation and run registration would all have failed. Fixed in
   `acc58e5`, with a regression test.
2. **Session-note reference.** The adapter looked for a `note_<ULID>`
   reference, but the owner returns an integer `note_id` (a global sequence, not per run). The
   first matching id in the session text is also the predecessor's, because
   the context read prints it first. Fixed in `42932e5` by taking the last
   JSON `note_id` (live from F1 onward, `run_91YN…/note/4`), then superseded
   in `3a1b4a3`: session text is model-controlled, so the scan was removed and
   `result-linked` links only the run and the harness session. The notes are
   owner records read through the run (no served op returns a run's own
   notes to its owner).
3. **Hook file mode.** Two hooks were committed without the executable bit,
   which failed CI. Fixed in `a68044d`.

## Review round 2 (agentops #343 at `3a1b4a3`), re-verified live

Opus review of #343 found the successor ran with OpenCode's default agent (shell as
`agent` for any allowed sender), a forgeable body fence, the spoofable note scan above,
stuck `launched` records with no time limit or operator close, and permanent refusal on
temporary policy states. All fixed in `3a1b4a3` (85/85 adapter and hook tests).

- **Successor tool restriction (server-side).** Each successor session is created with a
  permission ruleset: deny `*`, allow only bash `sprintctl run context *` and
  `sprintctl run note *`. Probed against opencode 1.18.33 on devbox before the change:
  without the ruleset `touch` in the project directory succeeded (control); with it,
  `touch`, `sprintctl --version`, `sprintctl run context … && touch …` and a
  `$(touch …)` substitution inside an allowed `run note` were denied, files absent.
  These are commands the bash parser sees as separate commands. What the two allow
  rules did **not** deny is recorded in round 3 below.
- **Negative test on the fixed build.** A session made by the adapter's own
  `create_session` (ruleset verified on the session) was told to run `touch /tmp/x`,
  `touch /projects/dev/agentops/.m2c-m1-x` and `cat /etc/hostname`: 3/3 tool calls
  denied, 0 files created.
- **Hostile body through the inbox** (`msg_01M4XF553K9GKT0PBXWD6D70GP`, unit): the body
  asked for the same `touch` and tried to close the fence. The successor ran only
  `sprintctl run context` and `run note` (one env-prefixed variant was denied by the
  ruleset), wrote note 18, outcome `completed`; no file created.
- **Post-fix success path** (`msg_01M4XF55TMQZSS2H0EP3JAEAKY`, unit): completed, note 19,
  5 receipts; `result-linked` evidence is the run and the session.
- The state database of the running adapter gained the new columns in place on start.
- Owner records for these two messages (status, 5 receipts each, and the successor notes
  read back through a reader run) are in `records-postfix.json`.

## Review round 3 (agentops #343 at `6a10a24`), re-verified live

The round-2 ruleset (deny `*`, allow the two `sprintctl run` patterns) admits shell
syntax that is not a separate command. Probed on devbox with sessions made by the
adapter's own `create_session`; every result is in `permission-probes.json`.

| Probe (inside an allowed `sprintctl run note …`) | Round-2 ruleset | + metacharacter denies | Sandbox only (round-2 ruleset) |
|---|---|---|---|
| `> /tmp/m2c-redir` | ran, file written | denied | ran into the private /tmp; host file absent |
| `> <dir>/file`, `>> <dir>/file` | ran, files written | denied | session dir read-only: "Read-only file system" |
| `--note "$(< /etc/hostname)"`, `--note $(</etc/hostname)` | ran (file read) | denied | — |
| `--note "${HOME}"` | — | ran (added `$` deny) | — |
| newline then `touch` | — | denied | — |
| `<(cat …)`, `<<< …` | — | denied | — |

The final ruleset in `6a10a24` ends with bash denies for any command containing
`>` `<` `$` `` ` `` `;` `&` `|`. The last matching rule wins, so these override the two
allows. On the deployed build, 7/7 hostile forms were denied, 0 files were written, and a
plain `run note` still reached the owner (`run-not-found` for the dummy run). For the
read probes the model refused to run the overtly hostile reads in the sandbox-only
column, so those cells were checked by entering the service's namespaces directly
(`nsenter`). There ~/.ssh, ~/.config/{git-pat,claude-token}.env, the other Vuoro
credentials, `/projects/dev/appservice` and `/projects/dev/vuoro` were unreadable (7/7),
and writes to `.bashrc`, the agentops checkout and the sprintctl venv failed read-only.
The inbox credential is readable inside the sandbox because sprintctl needs it; the
`<` and `$` denies keep it out of notes.

**OS containment.** `opencode serve` runs as the sandboxed user unit
`ops/systemd/opencode-serve-inbox.service`:

- home and `/projects/dev` are empty tmpfs mounts;
- opencode's data, state and cache directories are bound back read-write;
- the agentops checkout, the sprintctl tool env, the inbox profile and its credential
  are bound back read-only;
- `ProtectSystem=strict`, `PrivateTmp`, `NoNewPrivileges` and `RestrictSUIDSGID` are set.

A user unit cannot apply the IP allow/deny lines ("not running as root"); the
gitops-nixos system unit is expected to enforce them.

**Adoption still works with the ruleset check.** F4 was re-run on `6a10a24` against the
sandboxed server (`msg_01M4XG686K2QSC121P1NT5CXPW`, unit after an in-process crash at
`session_created`). The single title-matched session carried exactly the ruleset and was
adopted. The successor ran only `run context` and `run note` and wrote note 21; 5 receipts,
`completed`.

## Not exercised

- Gateway outage (the vuoro-shared endpoint going unreachable during a run).
- Overlapping reservations and stale work revisions. The bounded effect here
  is a session note, which has an owner idempotency boundary but no
  reservation or revision fence.
- `opencode serve` crashing mid-session.
- A run that hangs: fixed in agentops #343 at `3a1b4a3` (`max_run_seconds`, abort, recorded
  `failed`/`run-timeout`), and `resolve --abandon` now closes a stuck `launched` record with an
  abort. Covered by unit tests, not induced live.

Two empty OpenCode sessions from F4b remain on devbox. They are idle,
unprompted and titled `vuoro-msg:msg_01M4XD770A2PHQYWT3D4GWMWN8`.
