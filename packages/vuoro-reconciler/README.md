# vuoro-reconciler

The trusted-side executor for E3 (agentops#2467) effect intents. It is the
only thing in the E2/E3 design that applies a diff, signs a commit or opens
a pull request: `packages/vuoro-mcp-edge`'s `propose_effect` only records a
proposal and has no credential and no route to this package (see
`vuoro_mcp_edge.effect_tools`'s module docstring and
`docs/plans/2026-09-26-e2-e3-shared-contract.md` section 7).

## Acceptance (TS-16)

A cloud caller never applies an effect; it only queues one. `propose_effect`
records an intent in state `proposed`, and `proposed -> accepted` is done
only on the trusted side, by an actor that is never the proposing principal
and never reachable through any edge/MCP tool:

- **Operator (default).** `vuoro-reconciler accept <intent_id> --operator
  <subject> --intent-source module:factory` shows the intent and its diff,
  asks for confirmation, and records `{kind: "operator", subject}`.
  `vuoro-reconciler reject <intent_id> ... --reason <text>` records a
  rejection. Accepting as the proposer is refused.
- **Auto-accept policy (opt-in, off by default).** `AutoAcceptConfig(path)`
  reads a trusted-side JSON file passed explicitly -- never the environment,
  never a cloud-callable tool:
  `{version, policies: [{id, enabled, workspace_id, repository?,
  effect_kinds: [...], path_globs?}]}`. A missing or empty file means off.
  `Reconciler(auto_accept=...)` evaluates it asynchronously at the start of
  each `run_once`, and records `{kind: "policy", policy_id, version, scope,
  config_digest}` as the acceptor.

### Trust assumption

Acceptance authority is **whoever holds the `IntentSource` credentials on
the trusted side**. `--operator` is a self-asserted subject: it is recorded
for attribution (`Vuoro-Accepted-By: operator:<subject>`) and compared
against `proposer_principal` so a proposer cannot accept its own intent
under its own name, but nothing in this package authenticates it. Keep the
`IntentSource` credentials (and the auto-accept config file) where only
operators can reach them.

The CLI shows every proposer-supplied field with non-printable characters
escaped visibly (`\x1b`, `\x0d`, `\u202e`, ...), so ESC/CR sequences or
bidi overrides cannot hide or reorder diff lines; the edge also refuses
them in `title` and `rationale`.

A `PolicyAcceptor` is honoured only if it still matches the config the
reconciler is running with: the policy exists and is enabled, and the
recorded version, config digest and scope are current. Each policy must
name a `repository` or `path_globs` (or both).

## Git isolation

Every git command runs through `gitenv.run_git`: `GIT_CONFIG_GLOBAL=/dev/null`,
`GIT_CONFIG_NOSYSTEM=1`, `GIT_ATTR_NOSYSTEM=1`, a fresh empty `HOME` and
`XDG_CONFIG_HOME`, and no inherited `GIT_*`, `GNUPGHOME` or `SSH_AUTH_SOCK`.
Signing configuration reaches git only through the explicit `SigningKey`
(its `env` carries e.g. `GNUPGHOME`, and may not set `GIT_*`). Ambient
filter drivers, hooks, attribute files, aliases and credential helpers
therefore never apply to a proposer's diff. Git control files
(`.gitattributes`, `.gitmodules`, `.mailmap`, `.gitignore`, any other
`.git*` name, `info/attributes`-style paths) are refused before the diff is
applied and again on the staged result, whatever the path allowlist says.

Binary content is refused three times over, because a `GIT binary patch`
literal is base85 (no NUL) and a base-commit attribute such as `*.md diff`
makes git's own binary detection say "text":

1. before applying, any NUL byte or binary-patch line (`GIT binary patch`,
   `literal `, `delta `, `Binary files `) in the patch text;
2. before applying, any hunk that `git apply --numstat`, parsing the patch
   outside the checkout, reports as binary (git apply has no switch to
   turn binary support off, so a binary hunk simply never reaches it);
3. after applying, any staged blob containing NUL, a C0/C1 control
   character other than tab and LF, DEL, or invalid UTF-8.

The accept/reject view frames proposer text: a header and footer line,
every rationale line prefixed `| `, every diff line prefixed `> `, and
single-line fields with newlines escaped, so proposer text cannot produce a
frame line. Backslashes are shown as `\\`, so the text `\x1b` cannot pass
for an escaped ESC.

## What it does

1. Applies auto-accept (if configured) to `poll_proposed()`, then polls
   accepted intents through the `IntentSource` protocol. An accepted intent
   without an acceptor, or accepted by its own proposer, is refused.
2. Clones the intent's repository (with `core.hooksPath=/dev/null`) at a
   clean checkout of `base_commit` and applies `unified_diff`. A diff that
   does not apply cleanly is refused before anything is committed.
3. Re-validates what the diff actually changed against the reconciler's
   own `DiffPolicy` (binary, mode changes including new executable files,
   symlinks, submodules, deletes outside the path allowlist, CI workflow
   and protected paths), and a policy acceptor's recorded scope -- it does
   not trust the edge's validation.
4. Commits the result, signed by the reconciler's own key and committer
   identity (SSH or GPG, injected at runtime -- this package never mints or
   stores one), with trailers `Vuoro-Run: <run_id>`, `Vuoro-Intent:
   <intent_id>` and `Vuoro-Accepted-By: operator:<subject>` or
   `policy:<id>@<version>`.
5. Pushes a new branch and opens a pull request through the `ProviderClient`
   protocol, restricted to a repository allowlist. It never merges and
   never pushes a protected or default branch. A re-run that finds
   `vuoro-effect/<intent_id>` already carrying the same change is success.
6. Reports the outcome (`applied` with its acceptor, or `failed`) back
   through `IntentSource`. One intent's failure (checkout, apply, policy,
   commit, push, PR) is recorded and the others continue.

## Location independence

Where this package runs (a pod in a new `vuoro-effects` namespace, per the
pending trusted-service-boundary design) is not decided here. This package
takes its `IntentSource`, `ProviderClient` and signing key as constructor
arguments and makes no assumption about its deployment: no Kubernetes
manifest, no credential material and no network call to a real provider
lives in this repository.
