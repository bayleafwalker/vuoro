# Vuoro Workflow

`vuoro-workflow` watches an identified Forgejo Actions run and stores its
observations in a host-local SQLite database. It records CI and promotion
observations for an owning thread. Forgejo reports execution, the credential
broker authorizes merges, and the deployment system reports what is serving.

## Watch a pull request

Run this from the Vuoro checkout during development, or install the standalone
wheel and substitute `vuoro-workflow` for `uv run --package vuoro-workflow
vuoro-workflow`. Use your canonical Forgejo HTTPS origin and authorized
repository. The broker supplies a temporary `repo.read` credential. Use the
API run ID, which can differ from the visible run number.

```sh
uv run --package vuoro-workflow vuoro-workflow watch \
  --origin https://forge.example.org --repository owner/repository \
  --pr 42 --base main --head <40-hex-sha> \
  --run <api-run-id> --workflow ci.yaml --event pull_request \
  --required tests --required merge-gate --thread <owning-thread-id>
vuoro-workflow reconcile
vuoro-workflow --json status
vuoro-workflow --json inbox --thread <owning-thread-id>
vuoro-workflow ack --thread <owning-thread-id> <event-id>
```

Supply the required checks from repository policy. A passing job alone cannot
produce a success receipt. The workflow itself and every expected check on its
latest observed attempt must pass, against the exact PR head and base.
Provider failures and ambiguous responses produce `unavailable`, and a
changed or closed PR produces `superseded`. Restore the scope by registering
a new watch against the new head/run. A watch expires after one day by default.
`stop <watch-id>` stops polling and retains its earlier records.

Use `reconcile --interval 30` under a user service or run `reconcile` periodically.
The database defaults to `$XDG_STATE_HOME/vuoro-workflow/watch.sqlite3` or
`~/.local/state/vuoro-workflow/watch.sqlite3`. Pass `--state` before the command
to select another path. Watch registration is idempotent for a fixed scope.
The state is host-persistent. Back up that database separately if it needs to
survive host replacement.

## Watch a push

For post-merge CI or image publication, use `--event push` and pass the
run API's `prettyref` value to `--ref`. For a push to the main branch,
Forgejo reports `main`. Passing `refs/heads/main` for that run produces
`Forgejo workflow identity mismatch`. Keep the full commit SHA in `--head`
and use the exact job names returned by the run's jobs endpoint.

```sh
vuoro-workflow watch \
  --origin https://forge.example.org --repository owner/repository \
  --ref main --head <40-hex-sha> \
  --run <api-run-id> --workflow publish-image.yaml --event push \
  --required publish --thread <owning-thread-id>
```

After registration, check `vuoro-workflow --json status` for a matching
observation. If the ref is wrong, register the corrected rule and stop the
old watch by ID.

## Watch a promotion

After the existing approval path dispatches a promotion workflow, register
the exact API run, workflow filename, event, SHA and tag using
`--kind promotion --ref <tag>`. Also pass `--environment <name>` and
`--digest sha256:<digest>`. `--digest-input` selects the workflow input field
and defaults to `image_digest`. The adapter verifies the tag ref and digest
from the provider's workflow-dispatch event. The environment is the watch's
operator-supplied target. A successful observation establishes that the
specified workflow completed. Verify the promotion artifact and the applied
revision, served digest and health independently before claiming a live
rollout.

## Deliver observations

Each terminal state for a watch and attempt is written atomically to the
outbox. A consumer reads the owning thread's inbox, delivers the normalized
event using its trusted thread integration and acknowledges the event ID after
delivery. Retries can redeliver an event if delivery succeeded and the
acknowledgement failed. Consumers should deduplicate on the event ID and
recheck current repository and gate state before acting. Acknowledgements are
thread-scoped within the local service owner's authority.

The watcher provides a durable inbox for installations where background thread
resumption is unavailable. It stores no provider credentials, raw event bodies,
job logs or executable commands. Keep the state and its user service under a
trusted host account. The host must be online for timely observations.

This package does not dispatch workflows, merge PRs, sign tags, reconcile a
cluster or change admission. A success observation does not grant authority
to perform those actions.

## Run under systemd

The example user unit in `contrib/vuoro-workflow.service` assumes a dedicated
environment at `~/.local/share/vuoro-workflow/venv`. After building the wheel,
install it in that environment and register the unit.

```sh
uv venv ~/.local/share/vuoro-workflow/venv
uv pip install --python ~/.local/share/vuoro-workflow/venv/bin/python <wheel>
install -Dm644 packages/vuoro-workflow/contrib/vuoro-workflow.service \
  ~/.config/systemd/user/vuoro-workflow.service
systemctl --user daemon-reload
systemctl --user enable --now vuoro-workflow.service
```

The unit needs `credctl` in its PATH and a configured broker identity. If the
Python distribution does not find the system CA store, set `SSL_CERT_FILE` in a
user service drop-in to the host CA bundle. To inspect or disable:

```sh
journalctl --user -u vuoro-workflow.service
systemctl --user disable --now vuoro-workflow.service
```

## Integration boundary

A future API or MCP tool can offer the same typed registration, observation
and event acknowledgement operations to any harness. A small model can
translate a requested condition into a proposed rule. A trusted caller still
validates the fields, binds the destination thread, and selects supported
providers and repositories before it registers the rule. The watcher itself
evaluates typed rules. Arbitrary text conditions, automatic identity inference
and harness-specific thread resumption are outside this package.

### Opt in to Codex delivery

Install the wheel with its `codex` extra and use
`contrib/vuoro-workflow-codex@.service` to run the delivery adapter for one
explicit thread. The template targets a managed local app-server Unix socket.
For another trusted socket, configure the path in the service override.

```sh
uv pip install --python ~/.local/share/vuoro-workflow/venv/bin/python '<wheel>[codex]'
install -Dm644 packages/vuoro-workflow/contrib/vuoro-workflow-codex@.service \
  ~/.config/systemd/user/vuoro-workflow-codex@.service
systemctl --user daemon-reload
systemctl --user enable --now vuoro-workflow-codex@<owning-thread-id>.service
```

The adapter uses the thread's existing app-server configuration. It supplies
no approval, sandbox, model or working-directory overrides. Busy threads defer
delivery. Archived threads, ephemeral threads and threads that reject direct
input keep their events pending. Inspect receipts with
`vuoro-workflow --json deliveries --thread <owning-thread-id>`. An acknowledged
turn ID and event acknowledgement are recorded in
one transaction. When the server may have accepted a turn but its receipt is
missing, the event stays visible as `claimed-or-uncertain` and is excluded from
automatic retries. Check the thread for that event ID before acknowledging it
manually or continuing with the work.

## Shared wake API

`vuoro-workflow --state <database> wake-api --profile <trusted-profile.json>`
serves newline-delimited JSON-RPC 2.0 requests over stdio. A trusted launcher
chooses the profile and opens the channel for the caller. The caller can be a
model, a shell tool, or a harness bridge. An MCP bridge can map its tools to
these same methods.

The profile binds every request to a workspace, principal, harness instance,
destination and source allowlist. Keep the profile and database under the
trusted host user's control. Stdio prevents unbound network callers. This
process does not isolate mutually untrusted programs that can read or write
the same local files. A launcher serving mutually untrusted principals needs
a separate OS identity or equivalent file and channel isolation for each
principal.

An example profile for an inbox-only client:

```json
{
  "identity": {
    "workspace": "platform",
    "principal": "release-assistant",
    "harness": "agent-cli",
    "instance": "workstation"
  },
  "destination": {"kind": "inbox", "instance": "workstation"},
  "scopes": ["wake.read", "wake.write"],
  "max_ttl_seconds": 86400,
  "sources": {
    "cloud": {
      "origin": "https://forge.example.org",
      "repository": "owner/cloud",
      "policies": {
        "forgejo.ci.completed/v1": {
          "workflow": "ci.yaml",
          "event": "pull_request",
          "base": "main",
          "required": ["tests", "merge-gate"]
        }
      }
    }
  }
}
```

Use `"destination": {"kind": "codex", "instance": "workstation", "thread":
"<uuid>"}` for a trusted local Codex thread. A separately enabled relay makes
delivery automatic. Launch `codex-deliver --instance workstation` with the
same socket and thread arguments to consume that provider instance's channel.
Leave the instance argument unset for direct operator-registered watches.
The API reports that the destination is eligible for that
relay without making any claim about whether the relay is running. Inbox
destinations derive an internal channel from the profile identity and can
read receipts through the API.

The five methods accept JSON object parameters:

| Method | Scope | Parameters |
| --- | --- | --- |
| `wake.capabilities` | `wake.read` | Empty object |
| `wake.register` | `wake.write` | `template`, `source_alias`, typed `params`, `ttl_seconds`, `idempotency_key` |
| `wake.get` | `wake.read` | `rule_id` |
| `wake.receipts` | `wake.read` | `rule_id`, optional `cursor` and `limit` |
| `wake.cancel` | `wake.write` | `rule_id` |

Send a single bounded object per line with a request ID. The registration
response returns a durable `rule_id`. Repeat the same idempotency key with
the same condition and TTL to recover its rule after a lost response. An
attempt to reuse that key for a different resolved policy, condition or TTL
fails. Repeating a condition under a new key yields the existing rule with
its original lifetime. Use a new run identity for a new release attempt.
`wake.get` returns current rule status without disclosing the host source
origin or destination. `wake.receipts` returns historical terminal outcomes
and delivery state with a cursor local to the requested rule. Cancellation
ends future observations and suppresses events the relay has yet to claim.
The response reports unresolved delivery claims that might still arrive.
Reconcile or cancel old rules explicitly when changing a trusted profile.

The CI template takes `{"run": 42, "pr": 7, "head": "<full SHA>"}`. The
promotion template `forgejo.promotion.completed/v1` takes
`{"run": 43, "head": "<full SHA>", "ref": "<tag>", "digest": "sha256:<digest>"}`.
A promotion source policy supplies `workflow`, `event` set to
`workflow_dispatch`, `required`, `environment`, and optionally
`digest_input`. The API rejects request fields for origins, credentials,
destinations, required checks, commands, or free-form instructions.

An optional inexpensive planner can query capabilities and propose a typed
registration from a user request. For example, “wake me when PR 7's required
CI finishes” maps to the CI template after the launcher resolves the exact
run and full commit. The planner must ask for a missing identity or report an
unsupported condition. It has no authority to write the profile, access
provider credentials, start turns, or change release gates. Registering and
evaluating the resulting rule use deterministic code.
