"""The only way this package runs git: in a scrubbed environment.

The reconciler applies proposer-supplied diffs, so nothing ambient may
shape what git does with them: no global or system config (which could
define filter drivers, hooks, `core.attributesFile`, credential helpers,
aliases or URL rewrites), no inherited `GIT_*` variable, no `HOME` or
`XDG_CONFIG_HOME` pointing at someone's dotfiles, and no ambient signing
setup. Every call gets:

- `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and
  `GIT_ATTR_NOSYSTEM=1`;
- a fresh, empty `HOME` (and `XDG_CONFIG_HOME` under it), removed afterwards;
- no inherited `GIT_*`, `GNUPGHOME` or `SSH_AUTH_SOCK`;
- then `extra_env` -- in practice `SigningKey.env`, the one explicit
  channel for signing configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
import subprocess
import tempfile

__all__ = ["run_git", "scrubbed_env"]

_DROPPED = frozenset({"HOME", "XDG_CONFIG_HOME", "GNUPGHOME", "SSH_AUTH_SOCK", "EMAIL"})


def scrubbed_env(home: str, extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_") and name not in _DROPPED
    }
    env.update(
        {
            "HOME": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    env.update(extra_env or {})
    return env


def run_git(
    *args: str,
    cwd: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """`git *args`, never raising for a non-zero exit (callers check)."""

    with tempfile.TemporaryDirectory(prefix="vuoro-git-home-") as home:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=scrubbed_env(home, extra_env),
            capture_output=True,
            text=text,
        )
