"""Commit signing: SSH or GPG, injected at runtime.

This module never mints, stores or generates a key. `SigningKey` names one
that already exists (a GPG key id in some keyring, or an SSH signing key
file plus an allowed-signers file); tests construct a throwaway one and
pass it in the same way a real deployment would pass its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
import subprocess

from .gitenv import run_git

__all__ = ["SigningKey", "configure_signing", "verify_commit"]

KeyFormat = Literal["openpgp", "ssh"]

#: What `gitenv` pins for isolation; a key may not point git back at a
#: real home directory or config (every `GIT_*` is refused as well, which
#: covers GIT_CONFIG_*, GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE).
_ISOLATION_VARIABLES = frozenset({"HOME", "XDG_CONFIG_HOME"})


@dataclass(frozen=True)
class SigningKey:
    """The reconciler's own signing identity.

    `committer_name`/`committer_email` are the identity the reconciler
    commits as; they are set in the checkout's own config, so a commit never
    depends on (or picks up) an ambient global git identity.

    `signing_key` is a GPG key id/fingerprint for `key_format="openpgp"`,
    or a path to an SSH public key file for `key_format="ssh"`.
    `allowed_signers_file` is SSH-only: a `gpg.ssh.allowedSignersFile`-shaped
    file, needed to verify (not to sign). `env` carries whatever the signing
    backend needs (e.g. `GNUPGHOME`, `SSH_AUTH_SOCK`). It is the only
    signing input: every git call runs in `gitenv`'s scrubbed environment
    (no global/system config, fresh `HOME`, no inherited `GIT_*`,
    `GNUPGHOME` or `SSH_AUTH_SOCK`), with `env` merged on top. It may not
    set `GIT_*` variables, `HOME` or `XDG_CONFIG_HOME`.
    """

    key_format: KeyFormat
    signing_key: str
    committer_name: str
    committer_email: str
    allowed_signers_file: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Git itself is configured only through the checkout's own config
        # (configure_signing); `env` is for the signing backend (e.g.
        # GNUPGHOME), never a way to re-inject ambient git configuration.
        smuggled = sorted(
            name for name in self.env if name.startswith("GIT_") or name in _ISOLATION_VARIABLES
        )
        if smuggled:
            raise ValueError(f"SigningKey.env may not set git or isolation variables: {smuggled}")


def configure_signing(repo_path: str, key: SigningKey) -> None:
    """Set the checkout's git config so `git commit -S` commits as the
    reconciler's identity and signs with `key`."""

    _git(repo_path, "config", "--", "user.name", key.committer_name, key=key)
    _git(repo_path, "config", "--", "user.email", key.committer_email, key=key)
    _git(repo_path, "config", "--", "commit.gpgsign", "true", key=key)
    _git(repo_path, "config", "--", "gpg.format", key.key_format, key=key)
    _git(repo_path, "config", "--", "user.signingkey", key.signing_key, key=key)
    if key.key_format == "ssh":
        if not key.allowed_signers_file:
            raise ValueError("an ssh SigningKey needs allowed_signers_file to verify with")
        _git(repo_path, "config", "--", "gpg.ssh.allowedSignersFile", key.allowed_signers_file, key=key)


def verify_commit(repo_path: str, ref: str, key: SigningKey) -> bool:
    """True if `ref`'s signature verifies against `key`."""

    result = _git(repo_path, "verify-commit", "--end-of-options", ref, key=key, check=False)
    return result.returncode == 0


def _git(
    repo_path: str, *args: str, key: SigningKey, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = run_git("-C", repo_path, *args, extra_env=key.env)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args], result.stdout, result.stderr)
    return result
