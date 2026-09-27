# E1 review Routine: instructions

These are the instructions the E1 review Routine follows. Its prompt in
claude.ai is the stub at the end of this file, which points here, so a change
to this file changes the Routine's next fire. The run record names the
revision that ran: `recipe_id` holds this file's git blob and
`instruction_digest` holds its sha256.

Authoring rules: agentops `docs/runbooks/cloud-routine-authoring.md`. The
record steps below implement its "Record the run through the Vuoro connector"
section (agentops#2521, M1-1).

- **Slug:** `e1-review`
- **Report repository:** `bayleafwalker/vuoro` (this repository)
- **Connector:** Vuoro. Tools appear as `mcp__Vuoro__<tool>`.
- **Authority:** push a fresh branch and open a PR only. Do not merge, and do
  not push to `main`.

## Steps

Run these in order. Continue after a failed step unless the step says to stop,
and record every failure in the report.

### 1. Register the run

Compute, from a checkout of this repository at the commit the session started
on:

```sh
PROMPT=docs/routines/e1-review.md
BLOB=$(git rev-parse HEAD:$PROMPT)
DIGEST=$(sha256sum "$PROMPT" | cut -d' ' -f1)
STARTED=$(date -u +%Y%m%dT%H%M%SZ)   # take once, reuse everywhere below
claude --version   # harness_build; if the command is unavailable, use "unavailable"
```

Call `mcp__Vuoro__register_run` with:

```json
{
  "harness_id": "claude-code",
  "harness_build": "<claude --version output>",
  "model_id": "<the model id this session runs as>",
  "recipe_id": "bayleafwalker/vuoro:docs/routines/e1-review.md@<BLOB>",
  "observed_profile": {"instruction_digest": "sha256:<DIGEST>", "skill_digests": []},
  "idempotency_key": "routine.e1-review.<STARTED>"
}
```

Keep the returned `run_id`. Call the idempotency key `<RUN_KEY>` below.

If `register_run` is not listed or returns an error, do not stop. Note the
error code, or "tools not listed", for step 6, and skip every
`append_evidence` and `write_session_note` call.

### 2. Read the workspace's work state

1. Call `mcp__Vuoro__list_ready_work` with `{}`. Record the UTC time, the
   arguments and the raw result.
2. Call `mcp__Vuoro__describe_work` for each returned item, up to five. If the
   list is empty, call it for `work_id` 1, 2 and 3 instead. Record the same
   details for each call.

### 3. Judge

Answer one question: **did the hosted runtime authenticate and read live,
per-item workspace state through the connector?** Use yes, no or partial.
Base the answer only on the calls in step 2. Each distinct observation that
supports or undermines the answer is one finding, with ids `F1`, `F2` and so
on. Examples: an authority-scoped empty list with a fresh `as_of`; three
distinct items; an auth error; a malformed envelope.

### 4. Record each finding

For each finding `Fn`, call `mcp__Vuoro__append_evidence` with:

```json
{
  "run_id": "<run_id>",
  "kind": "finding",
  "ref": "docs/evidence/<YYYY-MM-DD>-e1-review-routine.md#Fn",
  "digest": "sha256:<sha256 of the finding paragraph>",
  "collector": "e1-review",
  "validity": {"basis": "until_inputs_change", "valid_from": "<now, ISO 8601 UTC>"},
  "idempotency_key": "<RUN_KEY>.f<n>"
}
```

The finding paragraph's bytes are the paragraph under `### Fn` in the report,
UTF-8, with no heading and no trailing newline, for example
`printf '%s' "$TEXT" | sha256sum`. Write that exact text into the report.

### 5. Write the report and record it

Write `docs/evidence/<YYYY-MM-DD>-e1-review-routine.md` with the following
sections:

- a title and a line with the session URL;
- `Run: <run_id>`, or `Run: unavailable (<reason>)`;
- **Calls made**: each call from step 2 with its time, arguments and raw result;
- **Findings**: each finding under a heading `### Fn`, followed by its
  one-paragraph text;
- **Verdict**: yes, no or partial, with one paragraph of reasoning;
- **Record**: the tools called in steps 1 and 4 and what each returned, or the
  error.

Then call `mcp__Vuoro__append_evidence` for the report itself:

```json
{
  "run_id": "<run_id>",
  "kind": "report",
  "ref": "docs/evidence/<YYYY-MM-DD>-e1-review-routine.md",
  "digest": "sha256:<sha256sum of the report file>",
  "collector": "e1-review",
  "validity": {"basis": "until_inputs_change", "valid_from": "<now, ISO 8601 UTC>"},
  "idempotency_key": "<RUN_KEY>.report"
}
```

Do not change the report file after this call.

### 6. Open the PR

Create a fresh branch `routine/e1-review-<STARTED>`. Commit only the report file,
push the branch, and run `gh pr create --base main` with:

- title: `E1 review Routine: <verdict> (<YYYY-MM-DD>)`;
- body: the verdict paragraph, a pointer to the report file, and as the last
  line, on its own:

  ```
  Vuoro-Run: <run_id>
  ```

  If `register_run` failed, the last line is
  `Vuoro-Run: unavailable (<reason>)`. If the run was registered but a later
  record call failed, keep the real `run_id` and name the failure in the
  report.

Open the PR in every case, including a "no" verdict and a run whose record
tools failed.

### 7. Close the run

Call `mcp__Vuoro__write_session_note` with:

```json
{
  "run_id": "<run_id>",
  "note": "E1 review <YYYY-MM-DD>: verdict <verdict>, <n> findings. PR <url>.\nVuoro-Run: <run_id>",
  "idempotency_key": "<RUN_KEY>.note"
}
```

## Routine stub (the prompt stored in claude.ai)

```text
You are the E1 review Routine for bayleafwalker/vuoro. Follow
docs/routines/e1-review.md in this repository exactly, from step 1 to step 7,
in a checkout at the commit you started on. Open a PR in every case. Never
merge and never push to main.
```

## Checking a run

From the trusted side, after the PR opens:

```sh
agentops routine-pr-conformance <pr-number> --records <export.json>
```

The check passes when the trailer resolves to this run, the RunManifest fields
are non-empty, and there is at least one evidence item.
