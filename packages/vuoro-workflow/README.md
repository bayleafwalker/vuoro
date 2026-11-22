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
