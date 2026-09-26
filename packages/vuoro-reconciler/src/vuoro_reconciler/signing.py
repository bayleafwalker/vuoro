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

__all__ = ["SigningKey", "configure_signing", "verify_commit"]

KeyFormat = Literal["openpgp", "ssh"]


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
    backend needs in-process (e.g. `GNUPGHOME` for an isolated keyring);
    it is merged over the ambient environment for every git invocation this
    package makes against the checkout.
    """

    key_format: KeyFormat
    signing_key: str
    committer_name: str
    committer_email: str
    allowed_signers_file: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)


def configure_signing(repo_path: str, key: SigningKey) -> None:
    """Set the checkout's git config so `git commit -S` commits as the
    reconciler's identity and signs with `key`."""

    _git(repo_path, "config", "user.name", key.committer_name, key=key)
    _git(repo_path, "config", "user.email", key.committer_email, key=key)
    _git(repo_path, "config", "commit.gpgsign", "true", key=key)
    _git(repo_path, "config", "gpg.format", key.key_format, key=key)
    _git(repo_path, "config", "user.signingkey", key.signing_key, key=key)
    if key.key_format == "ssh":
        if not key.allowed_signers_file:
            raise ValueError("an ssh SigningKey needs allowed_signers_file to verify with")
        _git(repo_path, "config", "gpg.ssh.allowedSignersFile", key.allowed_signers_file, key=key)


def verify_commit(repo_path: str, ref: str, key: SigningKey) -> bool:
    """True if `ref`'s signature verifies against `key`."""

    result = _git(repo_path, "verify-commit", ref, key=key, check=False)
    return result.returncode == 0


def _git(
    repo_path: str, *args: str, key: SigningKey, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    import os

    return subprocess.run(
        ["git", "-C", repo_path, *args],
        env={**os.environ, **key.env},
        check=check,
        capture_output=True,
    )
